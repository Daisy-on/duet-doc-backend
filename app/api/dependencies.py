from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = getattr(request.app.state, "database_sessions", None)
    if factory is None:
        raise HTTPException(503, "Database is not configured")
    async with factory() as session:
        yield session


DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    session_id: UUID | None
    is_development: bool = False


async def get_current_user(
    request: Request,
    session: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    settings = request.app.state.settings
    is_development = False
    authorization = request.headers.get("Authorization")
    if credentials is not None:
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Authentication required")
        try:
            user_id, session_id = decode_access_token(credentials.credentials, settings)
        except ValueError as exc:
            raise HTTPException(401, "Invalid or expired access token") from exc
    elif authorization:
        raise HTTPException(401, "Authentication required")
    elif settings.app_env == "development" and settings.dev_auth_enabled:
        try:
            user_id = UUID(settings.dev_user_id)
        except ValueError as exc:
            raise HTTPException(500, "Development user ID is invalid") from exc
        session_id = None
        is_development = True
    else:
        raise HTTPException(401, "Authentication required")

    active_user = await session.scalar(
        text("SELECT id FROM users WHERE id=:id AND disabled_at IS NULL"), {"id": user_id}
    )
    if active_user is None:
        message = (
            "Development user missing or disabled"
            if is_development
            else "Invalid or expired access token"
        )
        raise HTTPException(401, message)
    return CurrentUser(
        user_id=user_id,
        session_id=session_id,
        is_development=is_development,
    )


AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]
