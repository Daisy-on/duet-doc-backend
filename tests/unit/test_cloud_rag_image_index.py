from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.rag_worker import load_source, process_job
from app.services.cloud_rag import _index_is_current, cloud_rag_plan
from app.services.cloud_rag_chunker import chunk_image_description
from app.services.rag_search import CURRENT_CLOUD_IMAGE


def test_image_index_requires_same_asset_and_vision_pipeline():
    row = {
        "status": "ready",
        "source_fingerprint": "image-md5",
        "embedding_model": "BAAI/bge-large-zh-v1.5",
        "embedding_dimension": 1024,
        "index_version": "bge-v2:qwen3-vl-flash",
    }
    assert _index_is_current(row, "image-md5", "image")
    assert not _index_is_current(row, "changed-md5", "image")
    assert not _index_is_current({**row, "index_version": "bge-v1"}, "image-md5", "image")
    assert not _index_is_current(
        {**row, "index_version": "bge-v1:qwen3-vl-flash"}, "image-md5", "image"
    )
    assert not _index_is_current(row, "image-md5", "text")
    assert "bge-v2:qwen3-vl-flash" in CURRENT_CLOUD_IMAGE


def test_image_description_preserves_boundaries_and_splits_long_lines():
    description = "第一段内容。" * 60 + "\n" + "异常堆栈" * 100

    chunks = chunk_image_description(description)

    assert len(chunks) > 1
    assert all(chunk.index == index for index, chunk in enumerate(chunks))
    assert all(0 < len(chunk.content) <= 320 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks).replace("\n", "") == description.replace(
        "\n", ""
    )
    assert chunk_image_description("  简短描述  ")[0].content == "简短描述"


@pytest.mark.asyncio
async def test_image_only_plan_counts_unindexed_referenced_assets_once():
    session = MagicMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    images = [
        {"asset_id": "asset-a", "md5_hex": "md5-a"},
        {"asset_id": "asset-b", "md5_hex": "md5-b"},
    ]
    with (
        patch("app.services.cloud_rag.workspace_for_user", new=AsyncMock()),
        patch("app.services.cloud_rag._current_sources", new=AsyncMock(return_value=[])),
        patch("app.services.cloud_rag._current_images", new=AsyncMock(return_value=images)),
        patch(
            "app.services.cloud_rag._client_indexed_revisions",
            new=AsyncMock(return_value={}),
        ),
    ):
        plan = await cloud_rag_plan(session, uuid4(), uuid4(), False, True)

    assert plan.image_count == 2
    assert plan.document_count == 0
    assert plan.total_jobs == 2


@pytest.mark.asyncio
async def test_image_job_skips_asset_without_current_reference():
    session = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = None
    session.execute.return_value = result
    job = {
        "modality": "image",
        "workspace_id": "workspace-id",
        "source_id": "asset-id",
        "source_fingerprint": "md5",
    }

    assert await load_source(session, job) is None
    query = str(session.execute.call_args.args[0])
    assert "document_media_refs" in query
    assert "doc.deleted_at IS NULL" in query


@pytest.mark.asyncio
async def test_image_job_stores_description_vector_and_asset_id():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    sessions.return_value.__aexit__ = AsyncMock(return_value=None)
    provider = MagicMock(model="BAAI/bge-large-zh-v1.5", dimension=1024)
    provider.embed_text = AsyncMock(return_value=[0.0] * 1024)
    vision = MagicMock(model="qwen3-vl-flash")
    vision.describe_image = AsyncMock(return_value="一张包含折线图的图片")
    media = MagicMock()
    media.sign_read.return_value = "https://oss.test/signed"
    job = {
        "id": "job-id",
        "run_id": "run-id",
        "modality": "image",
        "source_type": "image",
        "workspace_id": "workspace-id",
        "source_id": "asset-id",
        "source_revision": None,
        "source_fingerprint": "image-md5",
    }

    with (
        patch("app.rag_worker.load_source", new=AsyncMock(return_value={"object_key": "key"})),
        patch("app.rag_worker.finish_job", new=AsyncMock()),
    ):
        await process_job(sessions, provider, job, vision, media)

    vision.describe_image.assert_awaited_once_with("https://oss.test/signed")
    provider.embed_text.assert_awaited_once_with("一张包含折线图的图片")
    writes = [
        call
        for call in session.execute.call_args_list
        if "INSERT INTO rag_cloud_chunks" in str(call.args[0])
    ]
    assert len(writes) == 1
    assert writes[0].args[1]["asset"] == "asset-id"
    assert writes[0].args[1]["content"] == "一张包含折线图的图片"


@pytest.mark.asyncio
async def test_image_job_stores_multiple_chunks_for_one_asset():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    sessions.return_value.__aexit__ = AsyncMock(return_value=None)
    provider = MagicMock(model="BAAI/bge-large-zh-v1.5", dimension=1024)
    provider.embed_text = AsyncMock(return_value=[0.0] * 1024)
    vision = MagicMock(model="qwen3-vl-flash")
    vision.describe_image = AsyncMock(return_value="图片文字。" * 100)
    media = MagicMock()
    media.sign_read.return_value = "https://oss.test/signed"
    job = {
        "id": "job-id",
        "run_id": "run-id",
        "modality": "image",
        "source_type": "image",
        "workspace_id": "workspace-id",
        "source_id": "asset-id",
        "source_revision": None,
        "source_fingerprint": "image-md5",
    }

    with (
        patch("app.rag_worker.load_source", new=AsyncMock(return_value={"object_key": "key"})),
        patch("app.rag_worker.finish_job", new=AsyncMock()),
    ):
        await process_job(sessions, provider, job, vision, media)

    writes = [
        call
        for call in session.execute.call_args_list
        if "INSERT INTO rag_cloud_chunks" in str(call.args[0])
    ]
    assert len(writes) > 1
    assert provider.embed_text.await_count == len(writes)
    assert [call.args[1]["idx"] for call in writes] == list(range(len(writes)))
    assert all(call.args[1]["asset"] == "asset-id" for call in writes)
    assert all(len(call.args[1]["content"]) <= 320 for call in writes)
    assert "".join(call.args[1]["content"] for call in writes) == "图片文字。" * 100


@pytest.mark.asyncio
async def test_image_job_does_not_write_if_reference_disappears():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    sessions.return_value.__aexit__ = AsyncMock(return_value=None)
    provider = MagicMock()
    provider.embed_text = AsyncMock(return_value=[0.0] * 1024)
    vision = MagicMock()
    vision.describe_image = AsyncMock(return_value="图片描述")
    media = MagicMock()
    media.sign_read.return_value = "https://oss.test/signed"
    job = {
        "modality": "image",
        "workspace_id": "workspace-id",
        "source_id": "asset-id",
        "source_fingerprint": "md5",
    }

    with (
        patch(
            "app.rag_worker.load_source",
            new=AsyncMock(side_effect=[{"object_key": "key"}, None]),
        ),
        patch("app.rag_worker.finish_job", new=AsyncMock()) as finish,
    ):
        await process_job(sessions, provider, job, vision, media)

    finish.assert_awaited_once_with(session, job, "skipped")
    assert not any(
        "rag_cloud_chunks" in str(call.args[0]) for call in session.execute.call_args_list
    )
