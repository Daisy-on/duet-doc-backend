from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import text

from app.api.dependencies import AuthenticatedUser, DatabaseSession
from app.schemas.auth import (
    AuthResponse,
    AuthUser,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    WorkspaceSummary,
)
from app.services.auth_service import login_user, logout_user, refresh_user_session, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _validate_cookie_origin(request: Request) -> None:
    origin = request.headers.get("Origin")
    if origin and origin not in request.app.state.settings.cors_origins:
        raise HTTPException(403, "Origin is not allowed")


def _set_refresh_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=token,
        max_age=settings.auth_refresh_token_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_refresh_cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(
        key=settings.auth_refresh_cookie_name,
        httponly=True,
        secure=settings.auth_refresh_cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, response: Response, session: DatabaseSession
) -> AuthResponse:
    _validate_cookie_origin(request)
    issued = await register_user(session, body, request.app.state.settings)
    _set_refresh_cookie(response, request, issued.refresh_token)
    return issued.response


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, session: DatabaseSession
) -> AuthResponse:
    _validate_cookie_origin(request)
    issued = await login_user(session, body, request.app.state.settings)
    _set_refresh_cookie(response, request, issued.refresh_token)
    return issued.response


@router.post("/refresh", response_model=AuthResponse)
async def refresh(request: Request, response: Response, session: DatabaseSession) -> AuthResponse:
    _validate_cookie_origin(request)
    settings = request.app.state.settings
    token = request.cookies.get(settings.auth_refresh_cookie_name)
    if not token:
        raise HTTPException(401, "Refresh token is missing")
    issued = await refresh_user_session(session, token, settings)
    _set_refresh_cookie(response, request, issued.refresh_token)
    return issued.response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: DatabaseSession) -> None:
    _validate_cookie_origin(request)
    settings = request.app.state.settings
    await logout_user(session, request.cookies.get(settings.auth_refresh_cookie_name))
    _clear_refresh_cookie(response, request)


@router.get("/me", response_model=MeResponse)
async def me(current_user: AuthenticatedUser, session: DatabaseSession) -> MeResponse:
    user = (
        (
            await session.execute(
                text(
                    "SELECT u.id,u.display_name,ai.provider_subject AS username "
                    "FROM users u LEFT JOIN auth_identities ai ON ai.user_id=u.id "
                    "AND ai.provider='password' WHERE u.id=:id AND u.disabled_at IS NULL"
                ),
                {"id": current_user.user_id},
            )
        )
        .mappings()
        .one()
    )
    workspaces = (
        (
            await session.execute(
                text(
                    "SELECT id,name FROM workspaces WHERE owner_user_id=:user "
                    "ORDER BY created_at,id"
                ),
                {"user": current_user.user_id},
            )
        )
        .mappings()
        .all()
    )
    return MeResponse(
        user=AuthUser(id=user["id"], username=user["username"], display_name=user["display_name"]),
        workspaces=[WorkspaceSummary(id=row["id"], name=row["name"]) for row in workspaces],
    )
