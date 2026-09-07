from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import AuthenticatedUser
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


@router.get("/workspaces")
async def workspaces(current_user: AuthenticatedUser, session: Session):
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, name FROM workspaces WHERE owner_user_id=:user "
                    "ORDER BY created_at, id"
                ),
                {"user": current_user.user_id},
            )
        )
        .mappings()
        .all()
    )
    return {"workspaces": [dict(row) for row in rows]}


@router.post("/sync/push")
async def push_changes(
    body: PushRequest,
    current_user: AuthenticatedUser,
    session: Session,
):
    return await push(session, current_user.user_id, body)


@router.get("/sync/pull")
async def pull_changes(
    workspace_id: UUID,
    current_user: AuthenticatedUser,
    session: Session,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    return await pull(session, current_user.user_id, workspace_id, cursor, limit)


@router.get("/sync/status")
async def sync_status(
    workspace_id: UUID,
    current_user: AuthenticatedUser,
    session: Session,
):
    workspace = await workspace_for_user(session, workspace_id, current_user.user_id)
    return {
        "workspace_id": workspace_id,
        "current_sequence": workspace["sync_sequence"],
    }
