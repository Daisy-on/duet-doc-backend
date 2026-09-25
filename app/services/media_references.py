import json
from html.parser import HTMLParser

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ImageAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.asset_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        asset_id = dict(attrs).get("data-asset-id")
        if asset_id:
            self.asset_ids.add(asset_id)


def extract_document_asset_ids(content: str, content_format: str) -> set[str]:
    if content_format == "html":
        parser = ImageAssetParser()
        parser.feed(content)
        return parser.asset_ids

    try:
        root = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Invalid TipTap JSON content") from exc

    asset_ids: set[str] = set()

    def visit(node) -> None:
        if not isinstance(node, dict):
            return
        attrs = node.get("attrs")
        if node.get("type") == "image" and isinstance(attrs, dict):
            asset_id = attrs.get("assetId")
            if isinstance(asset_id, str) and asset_id:
                asset_ids.add(asset_id)
        children = node.get("content")
        if isinstance(children, list):
            for child in children:
                visit(child)

    visit(root)
    return asset_ids


async def replace_document_media_refs(
    session: AsyncSession,
    workspace_id,
    document_id: str,
    asset_ids: set[str],
) -> None:
    existing_ids = set(
        await session.scalars(
            text(
                "SELECT asset_id FROM document_media_refs "
                "WHERE workspace_id=:wid AND document_id=:doc"
            ),
            {"wid": workspace_id, "doc": document_id},
        )
    )

    if asset_ids:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT asset_id,status,gc_state FROM media_assets "
                        "WHERE workspace_id=:wid "
                        "AND asset_id=ANY(CAST(:asset_ids AS text[])) FOR UPDATE"
                    ),
                    {"wid": workspace_id, "asset_ids": sorted(asset_ids)},
                )
            )
            .mappings()
            .all()
        )
        ready_ids = {
            row["asset_id"]
            for row in rows
            if row["status"] == "ready" and row["gc_state"] == "ready"
        }
        unavailable = asset_ids - ready_ids
        if unavailable:
            raise HTTPException(
                409,
                {"code": "MEDIA_NOT_READY", "asset_ids": sorted(unavailable)},
            )

    await session.execute(
        text("DELETE FROM document_media_refs WHERE workspace_id=:wid AND document_id=:doc"),
        {"wid": workspace_id, "doc": document_id},
    )
    if asset_ids:
        await session.execute(
            text(
                "INSERT INTO document_media_refs (workspace_id,document_id,asset_id) "
                "SELECT :wid,:doc,unnest(CAST(:asset_ids AS text[]))"
            ),
            {"wid": workspace_id, "doc": document_id, "asset_ids": sorted(asset_ids)},
        )
        await session.execute(
            text(
                "UPDATE media_assets SET unreferenced_at=NULL "
                "WHERE workspace_id=:wid AND asset_id=ANY(CAST(:asset_ids AS text[]))"
            ),
            {"wid": workspace_id, "asset_ids": sorted(asset_ids)},
        )

    removed_ids = existing_ids - asset_ids
    if removed_ids:
        await session.execute(
            text(
                "DELETE FROM rag_cloud_source_indexes AS idx "
                "WHERE idx.workspace_id=:wid AND idx.modality='image' "
                "AND idx.source_id=ANY(CAST(:asset_ids AS text[])) "
                "AND NOT EXISTS (SELECT 1 FROM document_media_refs AS ref "
                "WHERE ref.workspace_id=idx.workspace_id AND ref.asset_id=idx.source_id)"
            ),
            {"wid": workspace_id, "asset_ids": sorted(removed_ids)},
        )
        await session.execute(
            text(
                "UPDATE media_assets AS asset SET unreferenced_at=COALESCE(unreferenced_at,now()) "
                "WHERE workspace_id=:wid AND asset_id=ANY(CAST(:asset_ids AS text[])) "
                "AND NOT EXISTS (SELECT 1 FROM document_media_refs AS ref "
                "WHERE ref.workspace_id=asset.workspace_id AND ref.asset_id=asset.asset_id)"
            ),
            {"wid": workspace_id, "asset_ids": sorted(removed_ids)},
        )
