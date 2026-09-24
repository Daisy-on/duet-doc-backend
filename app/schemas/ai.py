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
    TOOL = "tool"


class ContextSourceType(StrEnum):
    DOCUMENT = "document"
    MEMO = "memo"
    IMAGE = "image"
    SELECTION = "selection"


class ContextOrigin(StrEnum):
    MANUAL = "manual"
    LOCAL_RETRIEVAL = "local_retrieval"
    CLOUD_RETRIEVAL = "cloud_retrieval"


class AICapability(StrEnum):
    KNOWLEDGE_SEARCH = "knowledge_search"


class ToolChoice(StrEnum):
    NONE = "none"
    AUTO = "auto"


class AssistantToolName(StrEnum):
    SEARCH_KNOWLEDGE_BASE = "search_knowledge_base"


class StreamEventType(StrEnum):
    START = "start"
    REASONING_DELTA = "reasoning_delta"
    TEXT_DELTA = "text_delta"
    USAGE = "usage"
    TOOL_CALL = "tool_call"
    FINISH = "finish"
    ERROR = "error"


class AIToolCall(APIModel):
    id: str = Field(min_length=1, max_length=200)
    name: AssistantToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    reasoning_content: str | None = Field(default=None, max_length=50_000)


class AIToolContinuation(APIModel):
    tool_call: AIToolCall


class AIMessage(APIModel):
    role: MessageRole
    content: str = Field(default="", max_length=50_000)
    tool_calls: list[AIToolCall] = Field(default_factory=list, max_length=1)
    tool_call_id: str | None = Field(default=None, max_length=200)
    name: AssistantToolName | None = None
    reasoning_content: str | None = Field(default=None, max_length=50_000)

    @model_validator(mode="after")
    def validate_message(self) -> "AIMessage":
        if self.role == MessageRole.TOOL:
            if not self.tool_call_id or not self.name:
                raise ValueError("Tool messages require toolCallId and name.")
            return self
        if self.role == MessageRole.ASSISTANT and self.tool_calls:
            return self
        if not self.content:
            raise ValueError("Messages require content unless they contain a tool call.")
        return self


class AIContext(APIModel):
    source_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    source_type: ContextSourceType = ContextSourceType.DOCUMENT
    origin: ContextOrigin = ContextOrigin.MANUAL
    chunk_id: str | None = Field(default=None, max_length=200)
    chunk_index: int | None = Field(default=None, ge=0)
    heading_path: list[str] = Field(default_factory=list, max_length=10)
    score: float | None = None
    asset_id: str | None = Field(default=None, max_length=200)


class AIOptions(APIModel):
    thinking: bool = False
    max_tokens: int = Field(default=2_048, ge=1, le=32_768)
    temperature: float = Field(default=0.5, ge=0, le=2)


class AIMetadata(APIModel):
    session_id: str | None = Field(default=None, max_length=200)
    document_id: str | None = Field(default=None, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)


class AIRequest(APIModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    task: CloudAITask
    messages: list[AIMessage] = Field(default_factory=list, max_length=50)
    instruction: str | None = Field(default=None, max_length=2_000)
    selected_text: str | None = Field(default=None, max_length=50_000)
    contexts: list[AIContext] = Field(default_factory=list, max_length=20)
    capabilities: set[AICapability] = Field(default_factory=set, max_length=5)
    tool_choice: ToolChoice = ToolChoice.NONE
    tool_continuation: AIToolContinuation | None = None
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
        if self.capabilities and self.task != CloudAITask.CHAT:
            raise ValueError("AI capabilities are only available for chat requests.")
        if (
            self.tool_choice == ToolChoice.AUTO
            and AICapability.KNOWLEDGE_SEARCH not in self.capabilities
        ):
            raise ValueError("toolChoice auto requires the knowledge_search capability.")
        if self.tool_continuation and self.tool_choice != ToolChoice.NONE:
            raise ValueError("Tool continuation requests must disable further tool calls.")
        if self.tool_continuation and (
            AICapability.KNOWLEDGE_SEARCH not in self.capabilities
            or self.tool_continuation.tool_call.name != AssistantToolName.SEARCH_KNOWLEDGE_BASE
        ):
            raise ValueError("Invalid tool continuation.")
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
    tool_call: AIToolCall | None = None
    error: APIError | None = None

    def as_sse(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "data": self.model_dump_json(by_alias=True, exclude_none=True),
        }
