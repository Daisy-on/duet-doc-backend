import pytest
from pydantic import ValidationError

from app.schemas.rag import MAX_TEXT_INDEX_CHUNKS, TextIndexUpload


def chunk(index: int) -> dict:
    return {
        "id": f"chunk-{index}",
        "chunk_index": index,
        "heading_path": [],
        "content": "content",
        "content_hash": f"hash-{index}",
        "embedding": [0.0] * 768,
    }


def payload(count: int) -> dict:
    return {
        "source_revision": 1,
        "source_fingerprint": "fingerprint",
        "embedding_model": "multilingual-e5-base",
        "embedding_dimension": 768,
        "chunker_version": "v2",
        "chunks": [chunk(index) for index in range(count)],
    }


def test_text_index_accepts_more_than_legacy_chunk_limit() -> None:
    index = TextIndexUpload.model_validate(payload(503))
    assert len(index.chunks) == 503


def test_text_index_rejects_unbounded_chunk_count() -> None:
    with pytest.raises(ValidationError):
        TextIndexUpload.model_validate(payload(MAX_TEXT_INDEX_CHUNKS + 1))
