import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TEXT_INDEX_CHUNKS = 2_000


class RagModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextIndexChunk(RagModel):
    id: str = Field(min_length=1, max_length=300)
    chunk_index: int = Field(ge=0)
    heading_path: list[str] = Field(default_factory=list, max_length=20)
    content: str = Field(min_length=1, max_length=50_000)
    content_hash: str = Field(min_length=1, max_length=128)
    embedding: list[float] = Field(min_length=1024, max_length=1024)

    @model_validator(mode="after")
    def validate_values(self):
        if any(len(heading) > 500 for heading in self.heading_path):
            raise ValueError("heading path entries must not exceed 500 characters")
        if not all(math.isfinite(value) for value in self.embedding):
            raise ValueError("embedding values must be finite")
        return self


class TextIndexUpload(RagModel):
    source_revision: int = Field(gt=0)
    source_fingerprint: str = Field(min_length=1, max_length=128)
    embedding_model: Literal["bge-large-zh-v1.5"]
    embedding_dimension: Literal[1024]
    chunker_version: Literal["v2"]
    chunks: list[TextIndexChunk] = Field(max_length=MAX_TEXT_INDEX_CHUNKS)

    @model_validator(mode="after")
    def validate_chunks(self):
        ids = [chunk.id for chunk in self.chunks]
        indexes = [chunk.chunk_index for chunk in self.chunks]
        if len(ids) != len(set(ids)) or len(indexes) != len(set(indexes)):
            raise ValueError("chunk ids and indexes must be unique")
        if sorted(indexes) != list(range(len(indexes))):
            raise ValueError("chunk indexes must be contiguous and start at zero")
        return self


class TextIndexStatus(RagModel):
    workspace_id: UUID
    source_id: str
    source_type: Literal["document", "memo"]
    source_revision: int
    source_fingerprint: str | None
    embedding_model: str | None
    embedding_dimension: int | None
    chunker_version: str | None
    status: Literal["pending", "ready", "stale", "error"]
    chunk_count: int
    indexed_at: datetime | None
    updated_at: datetime
