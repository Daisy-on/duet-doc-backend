import asyncio
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api.v1.sync import get_session
from app.core.config import Settings
from app.database import create_database
from app.main import create_app
from app.schemas.sync import PushRequest
from app.services.sync_service import push


@pytest_asyncio.fixture
async def sync_client():
    settings = Settings(dev_auth_enabled=True, dev_user_id=str(uuid4()))
    engine, sessions = create_database(settings)
    wid, other_wid, other_user = uuid4(), uuid4(), uuid4()
    async with sessions() as session:
        transaction = await session.begin()
        await session.execute(
            text("INSERT INTO users (id,display_name) VALUES (:a,'Test'),(:b,'Other')"),
            {"a": settings.dev_user_id, "b": other_user},
        )
        await session.execute(
            text(
                "INSERT INTO workspaces (id,owner_user_id,name) VALUES "
                "(:a,:u,'Test'),(:b,:v,'Other')"
            ),
            {"a": wid, "b": other_wid, "u": settings.dev_user_id, "v": other_user},
        )
        app = create_app(settings)

        async def override_session():
            async with session.begin_nested():
                yield session

        app.dependency_overrides[get_session] = override_session
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client, str(wid), str(other_wid), session
        finally:
            await transaction.rollback()
    await engine.dispose()


def kb(entity_id="kb", revision=0):
    return {
        "entity_type": "knowledge_base",
        "entity_id": entity_id,
        "operation": "upsert",
        "base_revision": revision,
        "data": {
            "name": "Notes",
            "description": "",
            "icon": "",
            "created_at": "2026-09-05T00:00:00Z",
        },
    }


def document():
    return {
        "entity_type": "document",
        "entity_id": "doc",
        "operation": "upsert",
        "base_revision": 0,
        "data": {
            "kb_id": "kb",
            "group_id": None,
            "title": "Note",
            "content": "hello",
            "content_format": "html",
            "created_at": "2026-09-05T00:00:00Z",
        },
    }


def chat_session(entity_id="session", revision=0):
    return {
        "entity_type": "chat_session",
        "entity_id": entity_id,
        "operation": "upsert",
        "base_revision": revision,
        "data": {
            "title": "Duet assistant",
            "is_pinned": False,
            "created_at": "2026-09-06T00:00:00Z",
            "updated_at": "2026-09-06T00:00:02Z",
        },
    }


def chat_message(status="complete"):
    return {
        "entity_type": "chat_message",
        "entity_id": "message",
        "operation": "upsert",
        "base_revision": 0,
        "data": {
            "session_id": "session",
            "role": "assistant",
            "content": "A synced answer",
            "status": status,
            "web_search_urls": [{"title": "Example", "url": "https://example.com"}],
            "referenced_docs": [{"id": "doc", "title": "Note"}],
            "knowledge_sources": [
                {
                    "source_id": "doc",
                    "source_type": "document",
                    "title": "Note",
                    "chunk_index": 0,
                    "heading_path": ["Section"],
                }
            ],
            "ai_metadata": {"provider": "deepseek", "usage": {"totalTokens": 42}},
            "created_at": "2026-09-06T00:00:01Z",
        },
    }


def mutation(wid, operations):
    return {"workspace_id": wid, "mutation_id": str(uuid4()), "operations": operations}


@pytest.mark.asyncio
async def test_idempotency_and_revision_conflict(sync_client):
    client, wid, _, session = sync_client
    body = mutation(wid, [kb()])
    first = await client.post("/api/v1/sync/push", json=body)
    assert first.status_code == 200
    retry = await client.post("/api/v1/sync/push", json=body)
    assert retry.json() == first.json()
    assert (
        await session.scalar(
            text("SELECT count(*) FROM sync_changes WHERE workspace_id=:wid"), {"wid": wid}
        )
        == 1
    )
    body["operations"][0]["data"]["name"] = "different"
    assert (await client.post("/api/v1/sync/push", json=body)).status_code == 409
    conflict = await client.post("/api/v1/sync/push", json=mutation(wid, [kb()]))
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_isolation(sync_client):
    client, _, other, _ = sync_client
    assert (await client.post("/api/v1/sync/push", json=mutation(other, [kb()]))).status_code == 404
    assert (
        await client.get("/api/v1/sync/pull", params={"workspace_id": other})
    ).status_code == 404
    assert (
        await client.get("/api/v1/sync/status", params={"workspace_id": other})
    ).status_code == 404
    workspaces = (await client.get("/api/v1/workspaces")).json()["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["id"] != other


@pytest.mark.asyncio
async def test_sync_status_tracks_workspace_sequence(sync_client):
    client, wid, _, _ = sync_client
    initial = await client.get("/api/v1/sync/status", params={"workspace_id": wid})
    assert initial.status_code == 200
    assert initial.json() == {"workspace_id": wid, "current_sequence": 0}

    pushed = await client.post("/api/v1/sync/push", json=mutation(wid, [kb()]))
    assert pushed.status_code == 200

    current = await client.get("/api/v1/sync/status", params={"workspace_id": wid})
    assert current.status_code == 200
    assert current.json() == {"workspace_id": wid, "current_sequence": 1}


@pytest.mark.asyncio
async def test_chat_session_and_message_sync(sync_client):
    client, wid, _, session = sync_client
    body = mutation(wid, [chat_message(), chat_session()])

    pushed = await client.post("/api/v1/sync/push", json=body)
    assert pushed.status_code == 200
    assert [item["entity_type"] for item in pushed.json()["results"]] == [
        "chat_session",
        "chat_message",
    ]

    retry = await client.post("/api/v1/sync/push", json=body)
    assert retry.json() == pushed.json()
    assert (
        await session.scalar(
            text("SELECT count(*) FROM chat_messages WHERE workspace_id=:wid"), {"wid": wid}
        )
        == 1
    )
    stored_activity_at = await session.scalar(
        text("SELECT updated_at FROM chat_sessions WHERE workspace_id=:wid AND id='session'"),
        {"wid": wid},
    )
    assert stored_activity_at.isoformat() == "2026-09-06T00:00:02+00:00"

    page = (await client.get("/api/v1/sync/pull", params={"workspace_id": wid})).json()
    assert [change["entity_type"] for change in page["changes"]] == [
        "chat_session",
        "chat_message",
    ]
    message_snapshot = page["changes"][1]["snapshot"]
    assert message_snapshot["content"] == "A synced answer"
    assert message_snapshot["ai_metadata"]["usage"]["totalTokens"] == 42

    delete_session = {
        "entity_type": "chat_session",
        "entity_id": "session",
        "operation": "delete",
        "base_revision": 1,
    }
    deleted = await client.post("/api/v1/sync/push", json=mutation(wid, [delete_session]))
    assert deleted.status_code == 200
    assert await session.scalar(
        text(
            "SELECT deleted_at IS NOT NULL FROM chat_messages "
            "WHERE workspace_id=:wid AND id='message'"
        ),
        {"wid": wid},
    )


@pytest.mark.asyncio
async def test_chat_message_requires_active_session_and_final_status(sync_client):
    client, wid, _, _ = sync_client
    missing_parent = await client.post("/api/v1/sync/push", json=mutation(wid, [chat_message()]))
    assert missing_parent.status_code == 409

    streaming = await client.post(
        "/api/v1/sync/push",
        json=mutation(wid, [chat_session(), chat_message(status="streaming")]),
    )
    assert streaming.status_code == 422


@pytest.mark.asyncio
async def test_delete_requires_complete_group_and_pagination(sync_client):
    client, wid, _, _ = sync_client
    assert (
        await client.post("/api/v1/sync/push", json=mutation(wid, [document(), kb()]))
    ).status_code == 200
    delete_kb = {
        "entity_type": "knowledge_base",
        "entity_id": "kb",
        "operation": "delete",
        "base_revision": 1,
    }
    assert (
        await client.post("/api/v1/sync/push", json=mutation(wid, [delete_kb]))
    ).status_code == 409
    delete_doc = {
        "entity_type": "document",
        "entity_id": "doc",
        "operation": "delete",
        "base_revision": 1,
    }
    assert (
        await client.post("/api/v1/sync/push", json=mutation(wid, [delete_kb, delete_doc]))
    ).status_code == 200
    cursor, count = 0, 0
    while True:
        response = await client.get(
            "/api/v1/sync/pull",
            params={
                "workspace_id": wid,
                "cursor": cursor,
                "limit": 1,
            },
        )
        assert response.status_code == 200
        page = response.json()
        assert page["next_cursor"] > cursor
        assert len(page["changes"]) == 2
        assert (page["changes"][0]["snapshot"]["deleted_at"] is not None) == (count == 1)
        cursor = page["next_cursor"]
        count += 1
        if not page["has_more"]:
            break
    assert count == 2


@pytest.mark.asyncio
async def test_group_cycle_is_atomic(sync_client):
    client, wid, _, _ = sync_client
    group = {
        "entity_type": "group",
        "entity_id": "g",
        "operation": "upsert",
        "base_revision": 0,
        "data": {
            "kb_id": "kb",
            "parent_group_id": "g",
            "name": "Cycle",
            "sort_order": 0,
            "depth": 0,
            "created_at": "2026-09-05T00:00:00Z",
        },
    }
    assert (
        await client.post("/api/v1/sync/push", json=mutation(wid, [kb(), group]))
    ).status_code == 409
    page = (await client.get("/api/v1/sync/pull", params={"workspace_id": wid})).json()
    assert page["changes"] == []


def test_production_rejects_development_identity():
    with pytest.raises(ValueError, match="restricted to development"):
        create_app(Settings(app_env="production", dev_auth_enabled=True))


@pytest.mark.asyncio
async def test_disabled_user_and_unknown_parent(sync_client):
    client, wid, _, session = sync_client
    assert (
        await client.post("/api/v1/sync/push", json=mutation(wid, [document()]))
    ).status_code == 409
    await session.execute(
        text(
            "UPDATE users SET disabled_at=now() WHERE id="
            "(SELECT owner_user_id FROM workspaces WHERE id=:wid)"
        ),
        {"wid": wid},
    )
    assert (await client.get("/api/v1/workspaces")).status_code == 401


@pytest.mark.asyncio
async def test_concurrent_retry_commits_only_once():
    engine, sessions = create_database(Settings())
    uid, wid = uuid4(), uuid4()
    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text("INSERT INTO users (id,display_name) VALUES (:id,'Concurrent test')"),
                {"id": uid},
            )
            await session.execute(
                text("INSERT INTO workspaces (id,owner_user_id,name) VALUES (:id,:uid,'Test')"),
                {"id": wid, "uid": uid},
            )
        body = PushRequest.model_validate(mutation(str(wid), [kb()]))

        async def send():
            async with sessions() as session, session.begin():
                return await push(session, uid, body)

        first, second = await asyncio.gather(send(), send())
        assert first == second
        async with sessions() as session:
            assert (
                await session.scalar(
                    text("SELECT sync_sequence FROM workspaces WHERE id=:id"), {"id": wid}
                )
                == 1
            )
    finally:
        async with sessions() as session, session.begin():
            for table in ("sync_mutations", "sync_changes", "knowledge_bases"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE workspace_id=:id"), {"id": wid}
                )
            await session.execute(text("DELETE FROM workspaces WHERE id=:id"), {"id": wid})
            await session.execute(text("DELETE FROM users WHERE id=:id"), {"id": uid})
        await engine.dispose()
