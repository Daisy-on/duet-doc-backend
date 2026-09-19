import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from app.api.dependencies import AuthenticatedUser, DatabaseSession
from app.schemas.media import MediaAccess, MediaState, UploadRequest, UploadResponse
from app.services.media_storage import MediaStorage
from app.services.oss_client import oss_service_error
from app.services.sync_service import workspace_for_user

router = APIRouter(prefix="/workspaces/{workspace_id}/media", tags=["media"])
logger = logging.getLogger(__name__)
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def get_media_storage(request: Request) -> MediaStorage:
    storage = getattr(request.app.state, "media_storage", None)
    if storage is None:
        raise HTTPException(503, "Media storage is not configured")
    return cast(MediaStorage, storage)


Storage = Annotated[MediaStorage, Depends(get_media_storage)]


async def storage_call[T](operation: Callable[[], T]) -> T:
    try:
        return await asyncio.to_thread(operation)
    except Exception as exc:
        service_error = oss_service_error(exc)
        if service_error is not None and service_error.status_code == 404:
            raise HTTPException(409, "Image upload is not complete") from exc
        # Do not log SDK exceptions: they can contain signed URLs and credentials.
        logger.warning(
            "OSS media request failed: type=%s status=%s code=%s",
            type(exc).__name__,
            service_error.status_code if service_error else None,
            service_error.code if service_error else None,
        )
        raise HTTPException(502, "Media storage request failed") from exc


async def load_asset(session, workspace_id: UUID, asset_id: str):
    row = (
        (
            await session.execute(
                text("SELECT * FROM media_assets WHERE workspace_id=:wid AND asset_id=:aid"),
                {"wid": workspace_id, "aid": asset_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Media asset not found")
    return row


@router.post("/uploads", response_model=UploadResponse)
async def request_upload(
    workspace_id: UUID,
    body: UploadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
    storage: Storage,
) -> UploadResponse:
    await workspace_for_user(session, workspace_id, user.user_id)
    if body.size_bytes > request.app.state.settings.media_max_size_bytes:
        raise HTTPException(413, "Image exceeds upload size limit")
    key = (
        f"media/v1/users/{user.user_id}/workspaces/{workspace_id}/"
        f"assets/{body.asset_id}/original.{EXTENSIONS[body.content_type]}"
    )
    await session.execute(
        text(
            "INSERT INTO media_assets "
            "(workspace_id,asset_id,object_key,content_type,size_bytes,md5_hex) "
            "VALUES (:wid,:asset_id,:key,:content_type,:size_bytes,:md5_hex) "
            "ON CONFLICT (workspace_id,asset_id) DO NOTHING"
        ),
        {**body.model_dump(), "wid": workspace_id, "key": key},
    )
    row = await load_asset(session, workspace_id, body.asset_id)
    if any(
        row[field] != getattr(body, field)
        for field in (
            "content_type",
            "size_bytes",
            "md5_hex",
        )
    ):
        raise HTTPException(409, "Asset ID already belongs to different image content")
    if row["status"] == "ready":
        await session.commit()
        return UploadResponse(asset_id=body.asset_id, status="ready")
    expires = datetime.now(UTC) + timedelta(
        seconds=request.app.state.settings.media_url_ttl_seconds
    )
    url, headers = await storage_call(
        lambda: storage.sign_upload(
            row["object_key"],
            body.content_type,
            body.md5_hex,
            expires,
        )
    )
    await session.commit()
    return UploadResponse(
        asset_id=body.asset_id,
        status="pending",
        upload_url=url,
        headers=headers,
        expires_at=expires,
    )


@router.post("/{asset_id}/complete", response_model=MediaState)
async def complete_upload(
    workspace_id: UUID,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    storage: Storage,
) -> MediaState:
    await workspace_for_user(session, workspace_id, user.user_id)
    row = await load_asset(session, workspace_id, asset_id)
    if row["status"] != "ready":
        size, content_type, etag = await storage_call(lambda: storage.head(row["object_key"]))
        if (
            size != row["size_bytes"]
            or content_type != row["content_type"]
            or (etag or "").strip('"').lower() != row["md5_hex"]
        ):
            raise HTTPException(
                409, "Uploaded image does not match declared size, type or checksum"
            )
        await session.execute(
            text(
                "UPDATE media_assets SET status='ready',ready_at=now(),unreferenced_at=now() "
                "WHERE workspace_id=:wid AND asset_id=:aid AND status='pending'"
            ),
            {"wid": workspace_id, "aid": asset_id},
        )
        await session.commit()
    return MediaState(asset_id=asset_id, status="ready")


@router.get("/{asset_id}/access", response_model=MediaAccess)
async def get_access(
    workspace_id: UUID,
    asset_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
    storage: Storage,
) -> MediaAccess:
    await workspace_for_user(session, workspace_id, user.user_id)
    row = await load_asset(session, workspace_id, asset_id)
    if row["status"] != "ready":
        raise HTTPException(409, "Image upload is not complete")
    expires = datetime.now(UTC) + timedelta(
        seconds=request.app.state.settings.media_url_ttl_seconds
    )
    url = await storage_call(lambda: storage.sign_read(row["object_key"], expires))
    return MediaAccess(asset_id=asset_id, url=url, expires_at=expires)
