from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.dependencies import get_database_session
from app.api.v1.sync import get_session
from app.core.config import Settings
from app.database import create_database
from app.main import create_app
from app.services.auth_service import bind_password_identity


@pytest_asyncio.fixture
async def auth_client():
    settings = Settings(dev_auth_enabled=False)
    engine, _ = create_database(settings)
    connection = await engine.connect()
    transaction = await connection.begin()
    sessions = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    app = create_app(settings)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_database_session] = override_session
    app.dependency_overrides[get_session] = override_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, sessions, app
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


def registration(username: str = "daisy_test") -> dict[str, str]:
    return {
        "username": username,
        "password": "correct-horse-battery-staple",
        "display_name": "Daisy",
    }


@pytest.mark.asyncio
async def test_register_provisions_identity_workspace_and_syncable_defaults(auth_client) -> None:
    client, sessions, _ = auth_client
    response = await client.post("/api/v1/auth/register", json=registration())

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["username"] == "daisy_test"
    assert response.cookies.get("duet_refresh_token")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["user"] == body["user"]
    assert me.json()["workspaces"] == [{"id": body["workspace_id"], "name": "Personal"}]

    async with sessions() as session:
        defaults = (
            (
                await session.execute(
                    text(
                        "SELECT w.sync_sequence,k.name,d.title,d.content_format,d.content "
                        "FROM workspaces w JOIN knowledge_bases k ON k.workspace_id=w.id "
                        "JOIN documents d ON d.workspace_id=w.id AND d.kb_id=k.id "
                        "WHERE w.id=:workspace"
                    ),
                    {"workspace": body["workspace_id"]},
                )
            )
            .mappings()
            .one()
        )
        assert defaults["sync_sequence"] == 2
        assert defaults["name"] == "我的知识库"
        assert defaults["title"] == "未命名文档"
        assert defaults["content_format"] == "tiptap_json"
        assert "开始书写" not in defaults["content"]
        assert (
            await session.scalar(
                text("SELECT count(*) FROM sync_changes WHERE workspace_id=:workspace"),
                {"workspace": body["workspace_id"]},
            )
            == 2
        )


@pytest.mark.asyncio
async def test_username_is_case_insensitive_and_login_error_is_generic(auth_client) -> None:
    client, _, _ = auth_client
    assert (
        await client.post("/api/v1/auth/register", json=registration("CaseUser"))
    ).status_code == 201
    duplicate = await client.post("/api/v1/auth/register", json=registration("caseuser"))
    assert duplicate.status_code == 409

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"username": "CaseUser", "password": "wrong"}
    )
    unknown_user = await client.post(
        "/api/v1/auth/login", json={"username": "unknown", "password": "wrong"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


@pytest.mark.asyncio
async def test_access_token_authorizes_workspace_api(auth_client) -> None:
    client, _, _ = auth_client
    registered = await client.post("/api/v1/auth/register", json=registration())
    body = registered.json()

    workspaces = await client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )

    assert workspaces.status_code == 200
    assert workspaces.json() == {"workspaces": [{"id": body["workspace_id"], "name": "Personal"}]}


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_reuse_revokes_family(auth_client) -> None:
    client, _, app = auth_client
    registered = await client.post("/api/v1/auth/register", json=registration())
    old_token = registered.cookies.get("duet_refresh_token")

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    new_token = refreshed.cookies.get("duet_refresh_token")
    assert old_token and new_token and new_token != old_token

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as replay_client:
        replay_client.cookies.set("duet_refresh_token", old_token, path="/api/v1/auth")
        assert (await replay_client.post("/api/v1/auth/refresh")).status_code == 401

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as family_client:
        family_client.cookies.set("duet_refresh_token", new_token, path="/api/v1/auth")
        assert (await family_client.post("/api/v1/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token_and_clears_cookie(auth_client) -> None:
    client, _, app = auth_client
    registered = await client.post("/api/v1/auth/register", json=registration())
    token = registered.cookies.get("duet_refresh_token")

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert "Max-Age=0" in logout.headers["set-cookie"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as refresh_client:
        refresh_client.cookies.set("duet_refresh_token", token, path="/api/v1/auth")
        assert (await refresh_client.post("/api/v1/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_cookie_endpoints_reject_untrusted_browser_origin(auth_client) -> None:
    client, _, _ = auth_client
    response = await client.post(
        "/api/v1/auth/register",
        json=registration(f"user_{uuid4().hex[:8]}"),
        headers={"Origin": "https://malicious.example"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_existing_development_user_can_be_bound_without_moving_workspace(
    auth_client,
) -> None:
    client, sessions, _ = auth_client
    user_id, workspace_id = uuid4(), uuid4()
    async with sessions() as session, session.begin():
        await session.execute(
            text("INSERT INTO users (id,display_name) VALUES (:id,'Existing user')"),
            {"id": user_id},
        )
        await session.execute(
            text(
                "INSERT INTO workspaces (id,owner_user_id,name) "
                "VALUES (:id,:user,'Existing workspace')"
            ),
            {"id": workspace_id, "user": user_id},
        )

    async with sessions() as session:
        await bind_password_identity(
            session,
            user_id,
            "Existing_User",
            "correct-horse-battery-staple",
            "Daisy",
        )

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "username": "existing_user",
            "password": "correct-horse-battery-staple",
        },
    )
    assert login.status_code == 200
    assert login.json()["user"]["id"] == str(user_id)
    assert login.json()["workspace_id"] == str(workspace_id)

    async with sessions() as session:
        with pytest.raises(ValueError, match="already has a password identity"):
            await bind_password_identity(
                session,
                user_id,
                "another_name",
                "correct-horse-battery-staple",
            )
