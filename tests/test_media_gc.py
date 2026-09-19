from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.database import create_database
from app.services.media_gc import MediaGCResult, list_media_gc_candidates, run_media_gc


class FakeStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fail: set[str] = set()

    def delete(self, key: str) -> None:
        if key in self.fail:
            raise RuntimeError("storage unavailable")
        self.deleted.append(key)


@pytest_asyncio.fixture
async def gc_database():
    engine, _ = create_database(Settings())
    async with engine.connect() as connection:
        transaction = await connection.begin()
        sessions = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        user_id, workspace_id = uuid4(), uuid4()
        await connection.execute(
            text("INSERT INTO users (id,display_name) VALUES (:id,'GC Test')"),
            {"id": user_id},
        )
        await connection.execute(
            text("INSERT INTO workspaces (id,owner_user_id,name) VALUES (:id,:uid,'GC Test')"),
            {"id": workspace_id, "uid": user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(workspace_id,id,revision,created_at,name,description,icon) "
                "VALUES (:wid,'kb',1,now(),'Test','','')"
            ),
            {"wid": workspace_id},
        )
        await connection.execute(
            text(
                "INSERT INTO documents "
                "(workspace_id,id,revision,created_at,kb_id,title,content,content_format) "
                "VALUES (:wid,'doc',1,now(),'kb','Test','','html')"
            ),
            {"wid": workspace_id},
        )
        try:
            yield sessions, connection, workspace_id
        finally:
            await transaction.rollback()
    await engine.dispose()


async def insert_asset(
    connection,
    workspace_id,
    asset_id: str,
    *,
    status: str = "ready",
    age: timedelta = timedelta(days=8),
    gc_state: str = "ready",
    referenced: bool = False,
) -> str:
    key = f"tests/{asset_id}.png"
    ready_at = datetime.now(UTC) - age if status == "ready" else None
    unreferenced_at = ready_at
    await connection.execute(
        text(
            "INSERT INTO media_assets "
            "(workspace_id,asset_id,object_key,content_type,size_bytes,md5_hex,status,"
            "created_at,ready_at,unreferenced_at,gc_state) "
            "VALUES (:wid,:aid,:key,'image/png',68,:md5,:status,:created,:ready,:orphan,:gc_state)"
        ),
        {
            "wid": workspace_id,
            "aid": asset_id,
            "key": key,
            "md5": "a" * 32,
            "status": status,
            "created": datetime.now(UTC) - age,
            "ready": ready_at,
            "orphan": unreferenced_at,
            "gc_state": gc_state,
        },
    )
    if referenced:
        await connection.execute(
            text(
                "INSERT INTO document_media_refs (workspace_id,document_id,asset_id) "
                "VALUES (:wid,'doc',:aid)"
            ),
            {"wid": workspace_id, "aid": asset_id},
        )
    return key


@pytest.mark.asyncio
async def test_gc_selects_only_expired_unreferenced_assets(gc_database):
    sessions, connection, workspace_id = gc_database
    old_ready = await insert_asset(connection, workspace_id, "old-ready")
    old_pending = await insert_asset(connection, workspace_id, "old-pending", status="pending")
    retry = await insert_asset(connection, workspace_id, "retry", gc_state="deleting")
    await insert_asset(connection, workspace_id, "recent", age=timedelta(days=1))
    await insert_asset(connection, workspace_id, "referenced", referenced=True)
    await insert_asset(connection, workspace_id, "legacy")
    await connection.execute(
        text(
            "UPDATE media_assets SET unreferenced_at=NULL "
            "WHERE workspace_id=:wid AND asset_id='legacy'"
        ),
        {"wid": workspace_id},
    )

    now = datetime.now(UTC)
    async with sessions() as session:
        candidates = await list_media_gc_candidates(
            session,
            now - timedelta(days=7),
            now - timedelta(hours=24),
            100,
        )
    assert {candidate.asset_id for candidate in candidates} == {
        "old-ready",
        "old-pending",
        "retry",
    }

    storage = FakeStorage()
    result = await run_media_gc(
        sessions,
        storage,
        now - timedelta(days=7),
        now - timedelta(hours=24),
        100,
    )
    assert result == MediaGCResult(selected=3, deleted=3, failed=0, skipped=0)
    assert set(storage.deleted) == {old_ready, old_pending, retry}
    remaining = set(
        await connection.scalars(
            text("SELECT asset_id FROM media_assets WHERE workspace_id=:wid"),
            {"wid": workspace_id},
        )
    )
    assert remaining == {"recent", "referenced", "legacy"}


@pytest.mark.asyncio
async def test_gc_failure_is_retried_from_deleting_state(gc_database):
    sessions, connection, workspace_id = gc_database
    key = await insert_asset(connection, workspace_id, "retry-after-failure")
    storage = FakeStorage()
    storage.fail.add(key)
    now = datetime.now(UTC)

    failed = await run_media_gc(
        sessions,
        storage,
        now - timedelta(days=7),
        now - timedelta(hours=24),
        100,
    )
    assert failed == MediaGCResult(selected=1, deleted=0, failed=1, skipped=0)
    assert (
        await connection.scalar(
            text(
                "SELECT gc_state FROM media_assets "
                "WHERE workspace_id=:wid AND asset_id='retry-after-failure'"
            ),
            {"wid": workspace_id},
        )
        == "deleting"
    )

    storage.fail.clear()
    retried = await run_media_gc(
        sessions,
        storage,
        now - timedelta(days=7),
        now - timedelta(hours=24),
        100,
    )
    assert retried == MediaGCResult(selected=1, deleted=1, failed=0, skipped=0)
    assert (
        await connection.scalar(
            text(
                "SELECT count(*) FROM media_assets "
                "WHERE workspace_id=:wid AND asset_id='retry-after-failure'"
            ),
            {"wid": workspace_id},
        )
        == 0
    )
