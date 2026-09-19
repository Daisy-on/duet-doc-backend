from uuid import uuid4

import httpx
import pytest_asyncio
from alibabacloud_oss_v2.exceptions import OperationError, ServiceError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.dependencies import CurrentUser, get_current_user, get_database_session
from app.api.v1.media import get_media_storage
from app.core.config import Settings
from app.database import create_database
from app.main import create_app


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.keys = []
        self.fail = False

    def sign_upload(self, key, content_type, md5_hex, expires_at):
        if self.fail:
            raise RuntimeError("unavailable")
        self.keys.append(key)
        return "https://oss.example/upload", {"Content-Type": content_type}

    def head(self, key):
        if key not in self.objects:
            raise OperationError(
                name="HeadObject",
                error=ServiceError(
                    status_code=404,
                    code="NoSuchKey",
                    request_id="test",
                    message="Not found",
                    ec="",
                    timestamp="",
                    request_target="",
                ),
            )
        return self.objects[key]

    def sign_read(self, key, expires_at):
        return "https://oss.example/read"


@pytest_asyncio.fixture
async def media_client():
    settings = Settings(media_max_size_bytes=1024)
    engine, _ = create_database(settings)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        sessions = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        user, workspace, other_user, other_workspace = (uuid4() for _ in range(4))
        for uid, wid in [(user, workspace), (other_user, other_workspace)]:
            await connection.execute(
                text("INSERT INTO users (id,display_name) VALUES (:id,'Media Test')"), {"id": uid}
            )
            await connection.execute(
                text(
                    "INSERT INTO workspaces (id,owner_user_id,name) VALUES (:id,:uid,'Media Test')"
                ),
                {"id": wid, "uid": uid},
            )
        app = create_app(settings)
        storage = FakeStorage()

        async def override_session():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_database_session] = override_session
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(user, None)
        app.dependency_overrides[get_media_storage] = lambda: storage
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                yield client, storage, workspace, other_workspace, connection, app
        finally:
            await transaction.rollback()
    await engine.dispose()


def upload_body():
    return {
        "asset_id": f"asset-{uuid4().hex[:12]}",
        "content_type": "image/png",
        "size_bytes": 68,
        "md5_hex": "a" * 32,
    }


async def test_media_lifecycle_retry_and_conflict(media_client):
    client, storage, workspace, _, connection, _ = media_client
    root = f"/api/v1/workspaces/{workspace}/media"
    body = upload_body()
    for _ in range(2):
        response = await client.post(root + "/uploads", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "pending"
    assert storage.keys[0] == storage.keys[1]
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM media_assets WHERE workspace_id=:wid"), {"wid": workspace}
        )
        == 1
    )
    asset = root + "/" + body["asset_id"]
    assert (await client.get(asset + "/access")).status_code == 409
    assert (await client.post(asset + "/complete")).status_code == 409
    for metadata in [
        (1, "image/png", "a" * 32),
        (68, "image/jpeg", "a" * 32),
        (68, "image/png", "b" * 32),
    ]:
        storage.objects[storage.keys[0]] = metadata
        assert (await client.post(asset + "/complete")).status_code == 409
    storage.objects[storage.keys[0]] = (68, "image/png", '"' + "A" * 32 + '"')
    for _ in range(2):
        response = await client.post(asset + "/complete")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    assert (await client.get(asset + "/access")).status_code == 200
    response = await client.post(root + "/uploads", json=body)
    assert response.json()["upload_url"] is None
    assert (
        await client.post(root + "/uploads", json={**body, "size_bytes": 69})
    ).status_code == 409


async def test_media_isolation_validation_and_storage_failure(media_client):
    client, storage, workspace, other_workspace, connection, app = media_client
    root = f"/api/v1/workspaces/{workspace}/media"
    body = upload_body()
    other_root = f"/api/v1/workspaces/{other_workspace}/media"
    assert (await client.post(other_root + "/uploads", json=body)).status_code == 404
    assert (await client.get(other_root + "/" + body["asset_id"] + "/access")).status_code == 404
    assert (await client.post(other_root + "/" + body["asset_id"] + "/complete")).status_code == 404
    for field in [
        {"content_type": "image/svg+xml"},
        {"md5_hex": "invalid"},
        {"object_key": "arbitrary/path"},
    ]:
        assert (await client.post(root + "/uploads", json={**body, **field})).status_code == 422
    assert (
        await client.post(root + "/uploads", json={**body, "size_bytes": 1025})
    ).status_code == 413
    storage.fail = True
    assert (await client.post(root + "/uploads", json=body)).status_code == 502
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM media_assets WHERE workspace_id=:wid"), {"wid": workspace}
        )
        == 0
    )
    del app.dependency_overrides[get_current_user]
    assert (await client.post(root + "/uploads", json=body)).status_code == 401
