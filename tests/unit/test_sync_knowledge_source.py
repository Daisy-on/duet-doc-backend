import pytest
from pydantic import ValidationError

from app.schemas.sync import KnowledgeSource


def test_knowledge_source_excerpt_is_optional_for_old_messages():
    source = KnowledgeSource(
        source_id="doc-1",
        source_type="document",
        title="Note",
        chunk_index=0,
        heading_path=["Section"],
    )
    assert source.excerpt is None


def test_knowledge_source_excerpt_is_preserved_and_bounded():
    data = {
        "source_id": "doc-1",
        "source_type": "document",
        "title": "Note",
        "chunk_index": 0,
        "heading_path": ["Section"],
        "excerpt": "A cited passage",
    }
    assert KnowledgeSource.model_validate(data).model_dump()["excerpt"] == "A cited passage"
    with pytest.raises(ValidationError):
        KnowledgeSource.model_validate({**data, "excerpt": "x" * 401})
