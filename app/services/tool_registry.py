import json
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import AIServiceError
from app.schemas.ai import (
    AICapability,
    AIRequest,
    AIToolCall,
    AssistantToolName,
    ContextSourceType,
)


class KnowledgeSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=1_000)
    source_types: list[ContextSourceType] = Field(default_factory=list, max_length=2)
    sort_by: str = Field(default="relevance", pattern="^(relevance|updatedAt)$")
    time_range_days: int | None = Field(default=None, ge=1, le=3650)
    top_k: int = Field(default=5, ge=1, le=8)


KNOWLEDGE_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": AssistantToolName.SEARCH_KNOWLEDGE_BASE.value,
        "description": (
            "Search the user's local knowledge base for documents and memos. "
            "Use this only when the answer depends on the user's own notes, documents, "
            "past decisions, or recent memos. Do not use it for general knowledge, writing, "
            "translation, or casual conversation."
        ),
        "parameters": KnowledgeSearchArguments.model_json_schema(),
    },
}

DSML_TOOL_CALL_MARKER = "<｜｜DSML｜｜tool_calls>"
_DSML_INVOKE_PATTERN = re.compile(
    r'<｜｜DSML｜｜invoke\s+name="(?P<name>[^"]+)">(?P<body>.*?)</｜｜DSML｜｜invoke>',
    re.DOTALL,
)
_DSML_PARAMETER_PATTERN = re.compile(
    r'<｜｜DSML｜｜parameter\s+name="(?P<name>[^"]+)"'
    r'(?:\s+string="(?P<string>true|false)")?>(?P<value>.*?)</｜｜DSML｜｜parameter>',
    re.DOTALL,
)


def provider_tools(request: AIRequest) -> list[dict[str, Any]]:
    if AICapability.KNOWLEDGE_SEARCH in request.capabilities:
        return [KNOWLEDGE_SEARCH_TOOL]
    return []


def parse_provider_tool_call(raw_call: dict[str, Any], request: AIRequest) -> AIToolCall:
    function = raw_call.get("function")
    if not isinstance(function, dict):
        raise AIServiceError(
            "INVALID_TOOL_CALL",
            "Cloud AI returned an invalid tool call.",
            status_code=502,
        )

    try:
        name = AssistantToolName(function.get("name"))
    except ValueError as exc:
        raise AIServiceError(
            "UNSUPPORTED_TOOL_CALL",
            "Cloud AI requested an unsupported tool.",
            status_code=502,
        ) from exc

    if (
        name != AssistantToolName.SEARCH_KNOWLEDGE_BASE
        or AICapability.KNOWLEDGE_SEARCH not in request.capabilities
    ):
        raise AIServiceError(
            "UNSUPPORTED_TOOL_CALL",
            "Cloud AI requested an unavailable tool.",
            status_code=502,
        )

    raw_arguments = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments)
        validated_arguments = KnowledgeSearchArguments.model_validate(arguments)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AIServiceError(
            "INVALID_TOOL_ARGUMENTS",
            "Cloud AI returned invalid tool arguments.",
            status_code=502,
        ) from exc

    tool_call_id = raw_call.get("id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise AIServiceError(
            "INVALID_TOOL_CALL",
            "Cloud AI returned a tool call without an ID.",
            status_code=502,
        )

    return AIToolCall(
        id=tool_call_id,
        name=name,
        arguments=validated_arguments.model_dump(by_alias=True, exclude_none=True),
    )


def parse_dsml_tool_call(content: str, request: AIRequest) -> AIToolCall | None:
    """Translate a model-emitted DSML fallback into the same validated tool call."""
    if DSML_TOOL_CALL_MARKER not in content:
        return None

    normalized_content = content.replace("\\</｜｜DSML", "</｜｜DSML")
    invoke_match = _DSML_INVOKE_PATTERN.search(normalized_content)
    if not invoke_match:
        raise AIServiceError(
            "INVALID_TOOL_CALL",
            "Cloud AI returned an invalid tool call.",
            status_code=502,
        )

    arguments: dict[str, Any] = {}
    for parameter_match in _DSML_PARAMETER_PATTERN.finditer(invoke_match.group("body")):
        name = parameter_match.group("name")
        value = parameter_match.group("value").strip()
        if parameter_match.group("string") == "false":
            try:
                arguments[name] = json.loads(value)
            except json.JSONDecodeError as exc:
                raise AIServiceError(
                    "INVALID_TOOL_ARGUMENTS",
                    "Cloud AI returned invalid tool arguments.",
                    status_code=502,
                ) from exc
        else:
            arguments[name] = value

    return parse_provider_tool_call(
        {
            "id": f"call_dsml_{uuid4().hex}",
            "function": {
                "name": invoke_match.group("name"),
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        },
        request,
    )
