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
        "embedding": [0.0] * 1024,
    }


def payload(count: int) -> dict:
    return {
        "source_revision": 1,
        "source_fingerprint": "fingerprint",
        "embedding_model": "bge-large-zh-v1.5",
        "embedding_dimension": 1024,
        "chunker_version": "v3",
        "chunks": [chunk(index) for index in range(count)],
    }


def test_text_index_accepts_more_than_legacy_chunk_limit() -> None:
    index = TextIndexUpload.model_validate(payload(503))
    assert len(index.chunks) == 503


def test_text_index_rejects_unbounded_chunk_count() -> None:
    with pytest.raises(ValidationError):
        TextIndexUpload.model_validate(payload(MAX_TEXT_INDEX_CHUNKS + 1))


def test_legacy_embedding_cannot_be_uploaded() -> None:
    old = payload(1)
    old["embedding_model"] = "multilingual-e5-base"
    old["embedding_dimension"] = 768
    old["chunks"][0]["embedding"] = [0.0] * 768
    with pytest.raises(ValidationError):
        TextIndexUpload.model_validate(old)
