from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.rag import RagSearchRequest
from app.services.rag_search import search_rag


def _session(has_index, rows=()):
    session = MagicMock()
    session.scalar = AsyncMock(return_value=has_index)
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_empty_index_does_not_call_embedding_provider():
    session = _session(False)
    provider = MagicMock()
    provider.embed_text = AsyncMock()
    with patch("app.services.rag_search.workspace_for_user", new=AsyncMock()):
        result = await search_rag(
            session,
            uuid4(),
            uuid4(),
            RagSearchRequest(query="文档中的图片", allow_cloud_embedding=True),
            provider,
        )

    assert not result.has_index
    assert result.hits == []
    provider.embed_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_query_requires_explicit_consent():
    session = _session(True)
    provider = MagicMock()
    provider.embed_text = AsyncMock()
    with patch("app.services.rag_search.workspace_for_user", new=AsyncMock()):
        with pytest.raises(HTTPException) as error:
            await search_rag(session, uuid4(), uuid4(), RagSearchRequest(query="数据库"), provider)

    assert error.value.status_code == 409
    provider.embed_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_vector_returns_referenced_image_without_cloud_embedding():
    row = {
        "source_id": "asset-1",
        "source_type": "image",
        "document_id": "doc-1",
        "kb_id": "kb-1",
        "title": "报告",
        "chunk_id": "asset-1:0",
        "chunk_index": 0,
        "heading_path": [],
        "content": "一张趋势图",
        "asset_id": "asset-1",
        "distance": 0.2,
        "source_updated_at": datetime.now(UTC),
        "index_origin": "image",
    }
    session = _session(True, [row])
    provider = MagicMock()
    provider.embed_text = AsyncMock()
    with patch("app.services.rag_search.workspace_for_user", new=AsyncMock()):
        result = await search_rag(
            session,
            uuid4(),
            uuid4(),
            RagSearchRequest(query="趋势图", embedding=[1.0] + [0.0] * 1023),
            provider,
        )

    assert result.hits[0].asset_id == "asset-1"
    assert result.hits[0].document_id == "doc-1"
    assert result.hits[0].score == pytest.approx(0.8)
    assert result.hits[0].neighbors == []
    session.execute.assert_awaited_once()
    provider.embed_text.assert_not_awaited()
    query = str(session.execute.call_args.args[0])
    assert "document_media_refs" in query
    assert "idx.source_revision=doc.revision" in query
    assert "idx.source_fingerprint=asset.md5_hex" in query


def test_query_vector_rejects_nonfinite_values():
    with pytest.raises(ValidationError):
        RagSearchRequest(query="test", embedding=[float("nan")] * 1024)
    with pytest.raises(ValidationError):
        RagSearchRequest(query="test", embedding=[0.0] * 1024)


@pytest.mark.asyncio
@pytest.mark.parametrize("index_origin", ["client", "cloud"])
async def test_text_hit_includes_only_same_heading_neighbors(index_origin):
    row = {
        "source_id": "doc-1",
        "source_type": "document",
        "document_id": "doc-1",
        "kb_id": "kb-1",
        "title": "报告",
        "chunk_id": "chunk-5",
        "chunk_index": 5,
        "heading_path": ["章节"],
        "content": "命中正文",
        "asset_id": None,
        "distance": 0.2,
        "source_updated_at": datetime.now(UTC),
        "index_origin": index_origin,
    }
    neighbors = [
        {
            "source_id": "doc-1",
            "anchor_index": 5,
            "index_origin": index_origin,
            "chunk_id": "chunk-4",
            "chunk_index": 4,
            "heading_path": ["章节"],
            "content": "答案正文",
        },
        {
            "source_id": "doc-1",
            "anchor_index": 5,
            "index_origin": index_origin,
            "chunk_id": "chunk-6",
            "chunk_index": 6,
            "heading_path": ["下个章节"],
            "content": "不应带入",
        },
    ]
    session = _session(True)
    hit_result = MagicMock()
    hit_result.mappings.return_value.all.return_value = [row]
    neighbor_result = MagicMock()
    neighbor_result.mappings.return_value.all.return_value = neighbors
    session.execute.side_effect = [hit_result, neighbor_result]
    with patch("app.services.rag_search.workspace_for_user", new=AsyncMock()):
        result = await search_rag(
            session,
            uuid4(),
            uuid4(),
            RagSearchRequest(query="答案", embedding=[1.0] + [0.0] * 1023),
            None,
        )

    assert [(neighbor.chunk_id, neighbor.content) for neighbor in result.hits[0].neighbors] == [
        ("chunk-4", "答案正文")
    ]
    query = str(session.execute.call_args.args[0])
    assert "idx.source_revision=doc.revision" in query
    assert "idx.chunker_version='v3'" in query
