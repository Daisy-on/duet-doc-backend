from uuid import uuid4

import pytest

from app.services import cloud_rag


class QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class Session:
    def __init__(self, cloud_rows, client_rows):
        self.results = [QueryResult(cloud_rows), QueryResult(client_rows), QueryResult([])]

    async def execute(self, _query, _params):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_coverage_reports_only_sources_without_any_current_index(monkeypatch):
    documents = [
        {
            "id": "doc-local",
            "title": "本地",
            "content": "a",
            "content_format": "html",
            "revision": 2,
        },
        {
            "id": "doc-stale",
            "title": "过期",
            "content": "b",
            "content_format": "html",
            "revision": 2,
        },
        {
            "id": "doc-cloud",
            "title": "云端",
            "content": "c",
            "content_format": "html",
            "revision": 2,
        },
    ]

    async def authorize(*_args):
        return None

    async def current_sources(*_args):
        return documents

    async def current_images(*_args):
        return [{"asset_id": "image-stale", "md5_hex": "new"}]

    monkeypatch.setattr(cloud_rag, "workspace_for_user", authorize)
    monkeypatch.setattr(cloud_rag, "_current_sources", current_sources)
    monkeypatch.setattr(cloud_rag, "_current_images", current_images)

    cloud_rows = [
        {
            "modality": "text",
            "source_id": "doc-local",
            "status": "stale",
            "source_fingerprint": "old",
            "embedding_model": "BAAI/bge-large-zh-v1.5",
            "embedding_dimension": 1024,
            "index_version": "bge-v3",
        },
        {
            "modality": "text",
            "source_id": "doc-cloud",
            "status": "ready",
            "source_fingerprint": cloud_rag.text_fingerprint("云端", "c", "html"),
            "embedding_model": "BAAI/bge-large-zh-v1.5",
            "embedding_dimension": 1024,
            "index_version": "bge-v3",
        },
        {
            "modality": "image",
            "source_id": "image-stale",
            "status": "stale",
            "source_fingerprint": "old",
            "embedding_model": "BAAI/bge-large-zh-v1.5",
            "embedding_dimension": 1024,
            "index_version": "bge-v2:qwen3-vl-flash",
        },
    ]
    client_rows = [
        {
            "source_id": "doc-local",
            "status": "ready",
            "source_revision": 2,
            "embedding_model": "bge-large-zh-v1.5",
            "embedding_dimension": 1024,
            "chunker_version": "v3",
        },
        {
            "source_id": "doc-stale",
            "status": "stale",
            "source_revision": 1,
            "embedding_model": "bge-large-zh-v1.5",
            "embedding_dimension": 1024,
            "chunker_version": "v3",
        },
        {
            "source_id": "doc-cloud",
            "status": "stale",
            "source_revision": 1,
            "embedding_model": "bge-large-zh-v1.5",
            "embedding_dimension": 1024,
            "chunker_version": "v3",
        },
    ]
    result = await cloud_rag.cloud_rag_coverage(Session(cloud_rows, client_rows), uuid4(), uuid4())
    assert result.has_any_index is True
    assert result.unavailable_stale_source_ids == ["doc-stale", "image-stale"]
