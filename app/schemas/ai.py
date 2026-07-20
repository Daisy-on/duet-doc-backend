from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CloudAITask(StrEnum):
    CHAT = "chat"
    REWRITE = "rewrite"
    EXPAND = "expand"
    EXPLAIN = "explain"
    SUMMARIZE = "summarize"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ContextSourceType(StrEnum):
    DOCUMENT = "document"
    SELECTION = "selection"


class StreamEventType(StrEnum):
    START = "start"
    REASONING_DELTA = "reasoning_delta"
    TEXT_DELTA = "text_delta"
    USAGE = "usage"
    FINISH = "finish"
    ERROR = "error"


class AIMessage(APIModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=50_000)


class AIContext(APIModel):
    source_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    source_type: ContextSourceType = ContextSourceType.DOCUMENT


class AIOptions(APIModel):
    thinking: bool = False
    max_tokens: int = Field(default=2_048, ge=1, le=32_768)
    temperature: float = Field(default=0.5, ge=0, le=2)


class AIMetadata(APIModel):
    session_id: str | None = Field(default=None, max_length=200)
    document_id: str | None = Field(default=None, max_length=200)


class AIRequest(APIModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    task: CloudAITask
    messages: list[AIMessage] = Field(default_factory=list, max_length=50)
    instruction: str | None = Field(default=None, max_length=2_000)
    selected_text: str | None = Field(default=None, max_length=50_000)
    contexts: list[AIContext] = Field(default_factory=list, max_length=20)
    options: AIOptions = Field(default_factory=AIOptions)
    metadata: AIMetadata | None = None

    @model_validator(mode="after")
    def validate_task_input(self) -> "AIRequest":
        if self.task == CloudAITask.CHAT and not self.messages:
            raise ValueError("Chat requests require at least one message.")
        if (
            self.task
            in {
                CloudAITask.REWRITE,
                CloudAITask.EXPAND,
                CloudAITask.EXPLAIN,
            }
            and not self.selected_text
        ):
            raise ValueError(f"{self.task.value} requests require selectedText.")
        if self.task == CloudAITask.SUMMARIZE and not (self.selected_text or self.contexts):
            raise ValueError("Summarize requests require selectedText or contexts.")
        return self


class AIUsage(APIModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AIResult(APIModel):
    request_id: str
    text: str
    reasoning_text: str | None = None
    provider: str
    model: str
    route_reason: str
    finish_reason: str | None = None
    total_latency_ms: float
    usage: AIUsage | None = None


class APIError(APIModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False


class ErrorResponse(APIModel):
    error: APIError


class AIStreamEvent(APIModel):
    event: StreamEventType
    request_id: str
    text: str | None = None
    provider: str | None = None
    model: str | None = None
    route_reason: str | None = None
    finish_reason: str | None = None
    ttft_ms: float | None = None
    total_latency_ms: float | None = None
    usage: AIUsage | None = None
    error: APIError | None = None

    def as_sse(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "data": self.model_dump_json(by_alias=True, exclude_none=True),
        }
