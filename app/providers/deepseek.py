import json
import logging
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import AIServiceError
from app.schemas.ai import (
    AIRequest,
    AIResult,
    AIStreamEvent,
    AIUsage,
    StreamEventType,
)

logger = logging.getLogger(__name__)


class DeepSeekProvider:
    provider_id = "deepseek"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    def _api_key(self) -> str:
        if self._settings.deepseek_api_key is None:
            raise AIServiceError(
                "AI_NOT_CONFIGURED",
                "Cloud AI is not configured.",
                status_code=503,
            )
        key = self._settings.deepseek_api_key.get_secret_value().strip()
        if not key:
            raise AIServiceError(
                "AI_NOT_CONFIGURED",
                "Cloud AI is not configured.",
                status_code=503,
            )
        return key

    def _payload(self, request: AIRequest, model: str, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "stream": stream,
            "max_tokens": request.options.max_tokens,
            "temperature": request.options.temperature,
            "thinking": {
                "type": "enabled" if request.options.thinking else "disabled",
            },
        }
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

    @property
    def _endpoint(self) -> str:
        return f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _usage(value: Any) -> AIUsage | None:
        if not isinstance(value, dict):
            return None
        return AIUsage(
            input_tokens=value.get("prompt_tokens"),
            output_tokens=value.get("completion_tokens"),
            total_tokens=value.get("total_tokens"),
        )

    @staticmethod
    def _raise_upstream_error(status_code: int) -> None:
        if status_code == 401:
            raise AIServiceError(
                "UPSTREAM_AUTH_ERROR",
                "Cloud AI authentication failed.",
                status_code=502,
            )
        if status_code == 429:
            raise AIServiceError(
                "UPSTREAM_RATE_LIMIT",
                "Cloud AI rate limit exceeded.",
                status_code=503,
                retryable=True,
            )
        if status_code >= 400:
            raise AIServiceError(
                "UPSTREAM_ERROR",
                "Cloud AI request failed.",
                status_code=502,
                retryable=status_code >= 500,
            )

    async def generate(self, request: AIRequest, model: str) -> AIResult:
        started_at = perf_counter()
        try:
            response = await self._client.post(
                self._endpoint,
                headers=self._headers(),
                json=self._payload(request, model, stream=False),
            )
            self._raise_upstream_error(response.status_code)
            body = response.json()
        except AIServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise AIServiceError(
                "UPSTREAM_TIMEOUT",
                "Cloud AI request timed out.",
                status_code=504,
                retryable=True,
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise AIServiceError(
                "UPSTREAM_UNAVAILABLE",
                "Cloud AI is temporarily unavailable.",
                status_code=502,
                retryable=True,
            ) from exc

        choices = body.get("choices") or []
        if not choices:
            raise AIServiceError(
                "INVALID_UPSTREAM_RESPONSE",
                "Cloud AI returned an invalid response.",
                status_code=502,
            )

        choice = choices[0]
        message = choice.get("message") or {}
        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "ai_request_completed provider=%s model=%s request_id=%s latency_ms=%.1f",
            self.provider_id,
            model,
            request.request_id,
            duration_ms,
        )
        return AIResult(
            request_id=request.request_id,
            text=message.get("content") or "",
            reasoning_text=message.get("reasoning_content"),
            provider=self.provider_id,
            model=model,
            route_reason="",
            finish_reason=choice.get("finish_reason"),
            total_latency_ms=duration_ms,
            usage=self._usage(body.get("usage")),
        )

    async def stream(self, request: AIRequest, model: str) -> AsyncIterator[AIStreamEvent]:
        started_at = perf_counter()
        first_token_ms: float | None = None
        finish_reason: str | None = None
        usage: AIUsage | None = None

        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                headers=self._headers(),
                json=self._payload(request, model, stream=True),
            ) as response:
                self._raise_upstream_error(response.status_code)
                yield AIStreamEvent(
                    event=StreamEventType.START,
                    request_id=request.request_id,
                    provider=self.provider_id,
                    model=model,
                )

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue

                    raw_data = line.removeprefix("data:").strip()
                    if raw_data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(raw_data)
                    except json.JSONDecodeError:
                        logger.warning(
                            "invalid_sse_chunk request_id=%s",
                            request.request_id,
                        )
                        continue

                    usage = self._usage(chunk.get("usage")) or usage
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue

                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}

                    reasoning = delta.get("reasoning_content")
                    content = delta.get("content")
                    if (reasoning or content) and first_token_ms is None:
                        first_token_ms = (perf_counter() - started_at) * 1000

                    if reasoning:
                        yield AIStreamEvent(
                            event=StreamEventType.REASONING_DELTA,
                            request_id=request.request_id,
                            text=reasoning,
                        )
                    if content:
                        yield AIStreamEvent(
                            event=StreamEventType.TEXT_DELTA,
                            request_id=request.request_id,
                            text=content,
                        )

        except AIServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise AIServiceError(
                "UPSTREAM_TIMEOUT",
                "Cloud AI request timed out.",
                status_code=504,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise AIServiceError(
                "UPSTREAM_UNAVAILABLE",
                "Cloud AI is temporarily unavailable.",
                status_code=502,
                retryable=True,
            ) from exc

        total_latency_ms = (perf_counter() - started_at) * 1000
        if usage is not None:
            yield AIStreamEvent(
                event=StreamEventType.USAGE,
                request_id=request.request_id,
                usage=usage,
            )
        yield AIStreamEvent(
            event=StreamEventType.FINISH,
            request_id=request.request_id,
            finish_reason=finish_reason,
            ttft_ms=first_token_ms,
            total_latency_ms=total_latency_ms,
        )
        logger.info(
            "ai_stream_completed provider=%s model=%s request_id=%s latency_ms=%.1f ttft_ms=%s",
            self.provider_id,
            model,
            request.request_id,
            total_latency_ms,
            f"{first_token_ms:.1f}" if first_token_ms is not None else "unknown",
        )
