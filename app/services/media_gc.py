import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ObjectDeleter(Protocol):
    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class MediaGCCandidate:
    workspace_id: UUID
    asset_id: str
    object_key: str
    status: str
    gc_state: str
    size_bytes: int


@dataclass(frozen=True)
class MediaGCResult:
    selected: int
    deleted: int
    failed: int
    skipped: int


async def list_media_gc_candidates(
    session: AsyncSession,
    ready_before: datetime,
    pending_before: datetime,
    limit: int,
) -> list[MediaGCCandidate]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT workspace_id,asset_id,object_key,status,gc_state,size_bytes "
                    "FROM media_assets AS asset WHERE gc_state='deleting' OR "
                    "(gc_state='ready' AND NOT EXISTS ("
                    "SELECT 1 FROM document_media_refs AS ref "
                    "WHERE ref.workspace_id=asset.workspace_id AND ref.asset_id=asset.asset_id"
                    ") AND ((status='ready' AND unreferenced_at IS NOT NULL "
                    "AND unreferenced_at<:ready_before) OR "
                    "(status='pending' AND created_at<:pending_before))) "
                    "ORDER BY CASE WHEN gc_state='deleting' THEN 0 ELSE 1 END,created_at,asset_id "
                    "LIMIT :limit"
                ),
                {
                    "ready_before": ready_before,
                    "pending_before": pending_before,
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    return [MediaGCCandidate(**row) for row in rows]


async def mark_media_for_deletion(
    session: AsyncSession,
    candidate: MediaGCCandidate,
    ready_before: datetime,
    pending_before: datetime,
) -> bool:
    if candidate.gc_state == "deleting":
        return True

    await session.execute(
        text("SELECT id FROM workspaces WHERE id=:wid FOR UPDATE"),
        {"wid": candidate.workspace_id},
    )
    marked = await session.scalar(
        text(
            "UPDATE media_assets AS asset SET gc_state='deleting' "
            "WHERE workspace_id=:wid AND asset_id=:aid AND gc_state='ready' "
            "AND NOT EXISTS (SELECT 1 FROM document_media_refs AS ref "
            "WHERE ref.workspace_id=asset.workspace_id AND ref.asset_id=asset.asset_id) "
            "AND ((status='ready' AND unreferenced_at IS NOT NULL "
            "AND unreferenced_at<:ready_before) OR "
            "(status='pending' AND created_at<:pending_before)) RETURNING 1"
        ),
        {
            "wid": candidate.workspace_id,
            "aid": candidate.asset_id,
            "ready_before": ready_before,
            "pending_before": pending_before,
        },
    )
    return marked == 1


async def run_media_gc(
    sessions: async_sessionmaker[AsyncSession],
    storage: ObjectDeleter,
    ready_before: datetime,
    pending_before: datetime,
    limit: int,
) -> MediaGCResult:
    async with sessions() as session:
        candidates = await list_media_gc_candidates(
            session,
            ready_before,
            pending_before,
            limit,
        )

    deleted = 0
    failed = 0
    skipped = 0
    for candidate in candidates:
        async with sessions() as session, session.begin():
            marked = await mark_media_for_deletion(
                session,
                candidate,
                ready_before,
                pending_before,
            )
        if not marked:
            skipped += 1
            continue

        try:
            await asyncio.to_thread(storage.delete, candidate.object_key)
        except Exception:
            failed += 1
            continue

        async with sessions() as session, session.begin():
            removed = await session.scalar(
                text(
                    "DELETE FROM media_assets AS asset "
                    "WHERE workspace_id=:wid AND asset_id=:aid AND gc_state='deleting' "
                    "AND NOT EXISTS (SELECT 1 FROM document_media_refs AS ref "
                    "WHERE ref.workspace_id=asset.workspace_id AND ref.asset_id=asset.asset_id) "
                    "RETURNING 1"
                ),
                {"wid": candidate.workspace_id, "aid": candidate.asset_id},
            )
        if removed == 1:
            deleted += 1
        else:
            skipped += 1

    return MediaGCResult(
        selected=len(candidates),
        deleted=deleted,
        failed=failed,
        skipped=skipped,
    )
