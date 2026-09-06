from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.sync import PushRequest
from app.services.sync_service import pull, push, workspace_for_user

router = APIRouter(tags=["sync"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = getattr(request.app.state, "database_sessions", None)
    if factory is None:
        raise HTTPException(503, "Database is not configured")
    async with factory() as session, session.begin():
        yield session


Session = Annotated[AsyncSession, Depends(get_session, scope="function")]


async def get_current_user(request: Request, session: Session) -> UUID:
    settings = request.app.state.settings
    if settings.app_env != "development" or not settings.dev_auth_enabled:
        raise HTTPException(401, "Authentication required")
    user_id = UUID(settings.dev_user_id)
    exists = await session.scalar(
        text("SELECT id FROM users WHERE id=:id AND disabled_at IS NULL"), {"id": user_id}
    )
    if exists is None:
        raise HTTPException(401, "Development user missing or disabled")
    return user_id


CurrentUser = Annotated[UUID, Depends(get_current_user, scope="function")]


@router.get("/workspaces")
async def workspaces(user: CurrentUser, session: Session):
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, name FROM workspaces WHERE owner_user_id=:user "
                    "ORDER BY created_at, id"
                ),
                {"user": user},
            )
        )
        .mappings()
        .all()
    )
    return {"workspaces": [dict(row) for row in rows]}


@router.post("/sync/push")
async def push_changes(
    body: PushRequest,
    user: CurrentUser,
    session: Session,
):
    return await push(session, user, body)


@router.get("/sync/pull")
async def pull_changes(
    workspace_id: UUID,
    user: CurrentUser,
    session: Session,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    return await pull(session, user, workspace_id, cursor, limit)


@router.get("/sync/status")
async def sync_status(
    workspace_id: UUID,
    user: CurrentUser,
    session: Session,
):
    workspace = await workspace_for_user(session, workspace_id, user)
    return {
        "workspace_id": workspace_id,
        "current_sequence": workspace["sync_sequence"],
    }
