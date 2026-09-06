from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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


class ChatSessionData(SyncModel):
    title: str = Field(min_length=1, max_length=500)
    is_pinned: bool = False
    created_at: datetime
    updated_at: datetime


class WebSearchUrl(SyncModel):
    title: str = Field(max_length=1000)
    url: str = Field(max_length=4096)


class ReferencedDoc(SyncModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(max_length=1000)


class KnowledgeSource(SyncModel):
    source_id: str = Field(min_length=1, max_length=200)
    source_type: Literal["document", "memo"]
    title: str = Field(max_length=1000)
    chunk_index: int = Field(ge=0)
    heading_path: list[str] = Field(default_factory=list, max_length=20)


class ChatMessageData(SyncModel):
    session_id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2_000_000)
    status: Literal["complete", "stopped", "error"]
    web_search_urls: list[WebSearchUrl] = Field(default_factory=list, max_length=100)
    referenced_docs: list[ReferencedDoc] = Field(default_factory=list, max_length=100)
    knowledge_sources: list[KnowledgeSource] = Field(default_factory=list, max_length=100)
    ai_metadata: dict[str, JsonValue] | None = None
    created_at: datetime


DATA_MODELS = {
    "knowledge_base": KnowledgeBaseData,
    "group": GroupData,
    "document": DocumentData,
    "chat_session": ChatSessionData,
    "chat_message": ChatMessageData,
}


class SyncOperation(SyncModel):
    entity_type: Literal["knowledge_base", "group", "document", "chat_session", "chat_message"]
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
            if isinstance(parsed, ChatSessionData) and parsed.updated_at.tzinfo is None:
                raise ValueError("updated_at must include timezone")
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
