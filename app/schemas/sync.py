from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SyncModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeBaseData(SyncModel):
    name: str = Field(min_length=1, max_length=500)
    description: str
    icon: str
    created_at: datetime


class GroupData(SyncModel):
    kb_id: str
    parent_group_id: str | None
    name: str = Field(min_length=1, max_length=500)
    sort_order: int
    depth: int = Field(ge=0, le=5)
    created_at: datetime


class DocumentData(SyncModel):
    kb_id: str
    group_id: str | None
    title: str = Field(max_length=1000)
    content: str = Field(max_length=2_000_000)
    content_format: Literal["tiptap_json", "html"]
    created_at: datetime


DATA_MODELS = {
    "knowledge_base": KnowledgeBaseData,
    "group": GroupData,
    "document": DocumentData,
}


class SyncOperation(SyncModel):
    entity_type: Literal["knowledge_base", "group", "document"]
    entity_id: str = Field(min_length=1, max_length=200)
    operation: Literal["upsert", "delete"]
    base_revision: int = Field(ge=0)
    data: dict | None = None

    @model_validator(mode="after")
    def validate_data(self):
        if self.operation == "upsert":
            if self.data is None:
                raise ValueError("upsert requires data")
            parsed = DATA_MODELS[self.entity_type].model_validate(self.data)
            if parsed.created_at.tzinfo is None:
                raise ValueError("created_at must include timezone")
            self.data = parsed.model_dump(mode="json")
        elif self.data is not None or self.base_revision == 0:
            raise ValueError("delete requires an existing revision and no data")
        return self


class PushRequest(SyncModel):
    workspace_id: UUID
    mutation_id: UUID
    operations: list[SyncOperation] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_entities(self):
        keys = [(op.entity_type, op.entity_id) for op in self.operations]
        if len(keys) != len(set(keys)):
            raise ValueError("Each entity may occur only once per mutation")
        return self
