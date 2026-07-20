from collections.abc import AsyncIterator

from app.core.config import Settings
from app.schemas.ai import (
    AIMessage,
    AIRequest,
    AIResult,
    AIStreamEvent,
    CloudAITask,
    MessageRole,
)
from app.services.ai_dispatcher import AIDispatcher


class StubProvider:
    def __init__(self) -> None:
        self.model = ""

    async def generate(self, request: AIRequest, model: str) -> AIResult:
        self.model = model
        return AIResult(
            request_id=request.request_id,
            text="ok",
            provider="stub",
            model=model,
            route_reason="",
            total_latency_ms=1,
        )

    async def stream(self, request: AIRequest, model: str) -> AsyncIterator[AIStreamEvent]:
        self.model = model
        if False:
            yield AIStreamEvent(event="start", request_id=request.request_id)


async def test_dispatcher_routes_chat_to_fast_model() -> None:
    settings = Settings(
        deepseek_fast_model="fast-model",
        deepseek_quality_model="quality-model",
    )
    provider = StubProvider()
    dispatcher = AIDispatcher(settings, provider)
    request = AIRequest(
        task=CloudAITask.CHAT,
        messages=[AIMessage(role=MessageRole.USER, content="hello")],
    )

    result = await dispatcher.generate(request)

    assert provider.model == "fast-model"
    assert result.route_reason == "fast-task"


async def test_dispatcher_routes_summary_to_quality_model() -> None:
    settings = Settings(
        deepseek_fast_model="fast-model",
        deepseek_quality_model="quality-model",
    )
    provider = StubProvider()
    dispatcher = AIDispatcher(settings, provider)
    request = AIRequest(task=CloudAITask.SUMMARIZE, selected_text="long document")

    await dispatcher.generate(request)

    assert provider.model == "quality-model"
