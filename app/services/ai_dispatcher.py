from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core.config import Settings
from app.core.exceptions import AIServiceError
from app.providers.base import AIProvider
from app.schemas.ai import AIRequest, AIResult, AIStreamEvent, CloudAITask, ToolChoice
from app.services.prompt_registry import build_messages


@dataclass(frozen=True, slots=True)
class RouteDecision:
    model: str
    reason: str


class AIDispatcher:
    def __init__(self, settings: Settings, provider: AIProvider) -> None:
        self._settings = settings
        self._provider = provider

    def _route(self, request: AIRequest) -> RouteDecision:
        if request.task == CloudAITask.SUMMARIZE or request.options.thinking:
            return RouteDecision(
                model=self._settings.deepseek_quality_model,
                reason="quality-task",
            )
        return RouteDecision(
            model=self._settings.deepseek_fast_model,
            reason="fast-task",
        )

    def _prepare(self, request: AIRequest) -> AIRequest:
        if len(request.messages) > self._settings.ai_max_messages:
            raise AIServiceError(
                "TOO_MANY_MESSAGES",
                "Too many messages were provided.",
                status_code=413,
            )
        messages = build_messages(request, self._settings.ai_max_context_chars)
        return request.model_copy(update={"messages": messages, "contexts": []})

    async def generate(self, request: AIRequest) -> AIResult:
        if request.tool_choice == ToolChoice.AUTO:
            raise AIServiceError(
                "TOOL_CALLS_REQUIRE_STREAMING",
                "Knowledge search requests must use the streaming endpoint.",
                status_code=400,
            )
        decision = self._route(request)
        result = await self._provider.generate(self._prepare(request), decision.model)
        return result.model_copy(update={"route_reason": decision.reason})

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamEvent]:
        decision = self._route(request)
        async for event in self._provider.stream(self._prepare(request), decision.model):
            yield event.model_copy(update={"route_reason": decision.reason})
