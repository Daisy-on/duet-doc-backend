from typing import cast

from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse

from app.core.exceptions import AIServiceError
from app.schemas.ai import (
    AIRequest,
    AIResult,
    AIStreamEvent,
    APIError,
    StreamEventType,
)
from app.services.ai_dispatcher import AIDispatcher

router = APIRouter(prefix="/ai", tags=["ai"])


def get_dispatcher(request: Request) -> AIDispatcher:
    return cast(AIDispatcher, request.app.state.ai_dispatcher)


@router.post("/generate", response_model=AIResult)
async def generate(body: AIRequest, request: Request) -> AIResult:
    return await get_dispatcher(request).generate(body)


@router.post("/stream")
async def stream(body: AIRequest, request: Request) -> EventSourceResponse:
    async def event_source():
        try:
            async for event in get_dispatcher(request).stream(body):
                if await request.is_disconnected():
                    return
                yield event.as_sse()
        except AIServiceError as exc:
            event = AIStreamEvent(
                event=StreamEventType.ERROR,
                request_id=body.request_id,
                error=APIError(
                    code=exc.code,
                    message=exc.message,
                    request_id=body.request_id,
                    retryable=exc.retryable,
                ),
            )
            yield event.as_sse()

    return EventSourceResponse(event_source(), ping=15)
