import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_and_update_password,
    verify_password,
)
from app.schemas.auth import AuthResponse, AuthUser, LoginRequest, RegisterRequest

DEFAULT_DOCUMENT_CONTENT = json.dumps(
    {"type": "doc", "content": [{"type": "paragraph"}]}, separators=(",", ":")
)
DUMMY_PASSWORD_HASH = hash_password("duet-doc-invalid-password")


@dataclass(frozen=True)
class IssuedAuth:
    response: AuthResponse
    refresh_token: str


def normalize_username(username: str) -> str:
    return username.strip().lower()


async def _create_refresh_session(
    session: AsyncSession,
    user_id: UUID,
    settings: Settings,
    *,
    family_id: UUID | None = None,
) -> tuple[UUID, str]:
    session_id = uuid4()
    refresh_token = generate_refresh_token()
    await session.execute(
        text(
            "INSERT INTO refresh_sessions "
            "(id,user_id,token_hash,token_family_id,expires_at) "
            "VALUES (:id,:user,:token_hash,:family,:expires_at)"
        ),
        {
            "id": session_id,
            "user": user_id,
            "token_hash": hash_refresh_token(refresh_token),
            "family": family_id or uuid4(),
            "expires_at": datetime.now(UTC) + timedelta(days=settings.auth_refresh_token_days),
        },
    )
    return session_id, refresh_token


def _auth_response(
    user_id: UUID,
    username: str | None,
    display_name: str,
    workspace_id: UUID,
    session_id: UUID,
    settings: Settings,
) -> AuthResponse:
    access_token, expires_in = create_access_token(user_id, session_id, settings)
    return AuthResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=AuthUser(id=user_id, username=username, display_name=display_name),
        workspace_id=workspace_id,
    )


async def register_user(
    session: AsyncSession, body: RegisterRequest, settings: Settings
) -> IssuedAuth:
    username = normalize_username(body.username)
    display_name = body.display_name.strip() if body.display_name else body.username
    user_id, identity_id, workspace_id = uuid4(), uuid4(), uuid4()
    kb_id = f"kb-{uuid4().hex[:12]}"
    document_id = f"doc-{uuid4().hex[:12]}"
    now = datetime.now(UTC)

    try:
        async with session.begin():
            await session.execute(
                text("INSERT INTO users (id,display_name) VALUES (:id,:display_name)"),
                {"id": user_id, "display_name": display_name},
            )
            await session.execute(
                text(
                    "INSERT INTO auth_identities (id,user_id,provider,provider_subject) "
                    "VALUES (:id,:user,'password',:subject)"
                ),
                {"id": identity_id, "user": user_id, "subject": username},
            )
            await session.execute(
                text(
                    "INSERT INTO password_credentials (user_id,password_hash) "
                    "VALUES (:user,:password_hash)"
                ),
                {"user": user_id, "password_hash": hash_password(body.password)},
            )
            await session.execute(
                text(
                    "INSERT INTO workspaces (id,owner_user_id,name,sync_sequence) "
                    "VALUES (:id,:user,'Personal',2)"
                ),
                {"id": workspace_id, "user": user_id},
            )
            await session.execute(
                text(
                    "INSERT INTO knowledge_bases "
                    "(workspace_id,id,revision,created_at,updated_at,name,description,icon) "
                    "VALUES (:wid,:id,1,:now,:now,'我的知识库','','book-open')"
                ),
                {"wid": workspace_id, "id": kb_id, "now": now},
            )
            await session.execute(
                text(
                    "INSERT INTO documents "
                    "(workspace_id,id,revision,created_at,updated_at,kb_id,group_id,"
                    "title,content,content_format) "
                    "VALUES (:wid,:id,1,:now,:now,:kb,NULL,'未命名文档',:content,'tiptap_json')"
                ),
                {
                    "wid": workspace_id,
                    "id": document_id,
                    "now": now,
                    "kb": kb_id,
                    "content": DEFAULT_DOCUMENT_CONTENT,
                },
            )

            kb_snapshot = {
                "workspace_id": str(workspace_id),
                "id": kb_id,
                "revision": 1,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "deleted_at": None,
                "name": "我的知识库",
                "description": "",
                "icon": "book-open",
            }
            document_snapshot = {
                "workspace_id": str(workspace_id),
                "id": document_id,
                "revision": 1,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "deleted_at": None,
                "kb_id": kb_id,
                "group_id": None,
                "title": "未命名文档",
                "content": DEFAULT_DOCUMENT_CONTENT,
                "content_format": "tiptap_json",
            }
            await session.execute(
                text(
                    "INSERT INTO sync_changes "
                    "(workspace_id,sequence,entity_type,entity_id,revision,operation,"
                    "group_end,snapshot) "
                    "VALUES (:wid,1,'knowledge_base',:kb,1,'upsert',2,CAST(:kb_snapshot AS jsonb)),"
                    "(:wid,2,'document',:document,1,'upsert',2,CAST(:document_snapshot AS jsonb))"
                ),
                {
                    "wid": workspace_id,
                    "kb": kb_id,
                    "document": document_id,
                    "kb_snapshot": json.dumps(kb_snapshot, ensure_ascii=False),
                    "document_snapshot": json.dumps(document_snapshot, ensure_ascii=False),
                },
            )
            refresh_session_id, refresh_token = await _create_refresh_session(
                session, user_id, settings
            )
    except IntegrityError as exc:
        raise HTTPException(409, "Username is already registered") from exc

    return IssuedAuth(
        response=_auth_response(
            user_id, username, display_name, workspace_id, refresh_session_id, settings
        ),
        refresh_token=refresh_token,
    )


async def login_user(session: AsyncSession, body: LoginRequest, settings: Settings) -> IssuedAuth:
    username = normalize_username(body.username)
    async with session.begin():
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.id,u.display_name,pc.password_hash "
                        "FROM auth_identities ai "
                        "JOIN users u ON u.id=ai.user_id "
                        "JOIN password_credentials pc ON pc.user_id=u.id "
                        "WHERE ai.provider='password' AND ai.provider_subject=:username "
                        "AND u.disabled_at IS NULL"
                    ),
                    {"username": username},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            verify_password(body.password, DUMMY_PASSWORD_HASH)
            raise HTTPException(401, "Invalid username or password")

        verified, updated_hash = verify_and_update_password(body.password, row["password_hash"])
        if not verified:
            raise HTTPException(401, "Invalid username or password")
        if updated_hash:
            await session.execute(
                text(
                    "UPDATE password_credentials SET password_hash=:password_hash,"
                    "password_changed_at=now() WHERE user_id=:user"
                ),
                {"password_hash": updated_hash, "user": row["id"]},
            )
        await session.execute(
            text(
                "UPDATE auth_identities SET last_login_at=now() "
                "WHERE provider='password' AND provider_subject=:username"
            ),
            {"username": username},
        )
        workspace_id = await session.scalar(
            text(
                "SELECT id FROM workspaces WHERE owner_user_id=:user ORDER BY created_at,id LIMIT 1"
            ),
            {"user": row["id"]},
        )
        if workspace_id is None:
            raise HTTPException(409, "User has no workspace")
        session_id, refresh_token = await _create_refresh_session(session, row["id"], settings)

    return IssuedAuth(
        response=_auth_response(
            row["id"], username, row["display_name"], workspace_id, session_id, settings
        ),
        refresh_token=refresh_token,
    )


async def refresh_user_session(
    session: AsyncSession, refresh_token: str, settings: Settings
) -> IssuedAuth:
    token_hash = hash_refresh_token(refresh_token)
    failure: str | None = None
    issued: IssuedAuth | None = None

    async with session.begin():
        row = (
            (
                await session.execute(
                    text(
                        "SELECT rs.*,u.display_name,u.disabled_at,ai.provider_subject AS username "
                        "FROM refresh_sessions rs JOIN users u ON u.id=rs.user_id "
                        "LEFT JOIN auth_identities ai ON ai.user_id=u.id "
                        "AND ai.provider='password' "
                        "WHERE rs.token_hash=:token_hash FOR UPDATE OF rs"
                    ),
                    {"token_hash": token_hash},
                )
            )
            .mappings()
            .first()
        )
        now = datetime.now(UTC)
        if row is None or row["disabled_at"] is not None:
            failure = "Invalid refresh token"
        elif row["revoked_at"] is not None:
            if row["replaced_by"] is not None:
                await session.execute(
                    text(
                        "UPDATE refresh_sessions SET revoked_at=COALESCE(revoked_at,now()) "
                        "WHERE token_family_id=:family"
                    ),
                    {"family": row["token_family_id"]},
                )
            failure = "Invalid refresh token"
        elif row["expires_at"] <= now:
            await session.execute(
                text("UPDATE refresh_sessions SET revoked_at=now() WHERE id=:id"), {"id": row["id"]}
            )
            failure = "Refresh token expired"
        else:
            workspace_id = await session.scalar(
                text(
                    "SELECT id FROM workspaces WHERE owner_user_id=:user "
                    "ORDER BY created_at,id LIMIT 1"
                ),
                {"user": row["user_id"]},
            )
            if workspace_id is None:
                failure = "User has no workspace"
            else:
                new_session_id, new_refresh_token = await _create_refresh_session(
                    session, row["user_id"], settings, family_id=row["token_family_id"]
                )
                await session.execute(
                    text(
                        "UPDATE refresh_sessions SET revoked_at=now(),replaced_by=:replacement "
                        "WHERE id=:id"
                    ),
                    {"replacement": new_session_id, "id": row["id"]},
                )
                issued = IssuedAuth(
                    response=_auth_response(
                        row["user_id"],
                        row["username"],
                        row["display_name"],
                        workspace_id,
                        new_session_id,
                        settings,
                    ),
                    refresh_token=new_refresh_token,
                )

    if failure or issued is None:
        raise HTTPException(401, failure or "Invalid refresh token")
    return issued


async def logout_user(session: AsyncSession, refresh_token: str | None) -> None:
    if not refresh_token:
        return
    async with session.begin():
        await session.execute(
            text(
                "UPDATE refresh_sessions SET revoked_at=COALESCE(revoked_at,now()) "
                "WHERE token_hash=:token_hash"
            ),
            {"token_hash": hash_refresh_token(refresh_token)},
        )


async def bind_password_identity(
    session: AsyncSession,
    user_id: UUID,
    username: str,
    password: str,
    display_name: str | None = None,
) -> None:
    normalized_username = normalize_username(username)
    try:
        async with session.begin():
            user = (
                (
                    await session.execute(
                        text("SELECT id FROM users WHERE id=:id FOR UPDATE"),
                        {"id": user_id},
                    )
                )
                .mappings()
                .first()
            )
            if user is None:
                raise ValueError("The development user does not exist")

            existing_identity = await session.scalar(
                text(
                    "SELECT provider_subject FROM auth_identities "
                    "WHERE user_id=:user AND provider='password'"
                ),
                {"user": user_id},
            )
            if existing_identity is not None:
                raise ValueError("The development user already has a password identity")

            await session.execute(
                text(
                    "INSERT INTO auth_identities (id,user_id,provider,provider_subject) "
                    "VALUES (:id,:user,'password',:subject)"
                ),
                {
                    "id": uuid4(),
                    "user": user_id,
                    "subject": normalized_username,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO password_credentials (user_id,password_hash) "
                    "VALUES (:user,:password_hash)"
                ),
                {"user": user_id, "password_hash": hash_password(password)},
            )
            if display_name is not None:
                await session.execute(
                    text("UPDATE users SET display_name=:name WHERE id=:id"),
                    {"name": display_name, "id": user_id},
                )
    except IntegrityError as exc:
        raise ValueError("The username is already registered") from exc
