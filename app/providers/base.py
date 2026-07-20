from collections.abc import AsyncIterator
from typing import Protocol

from app.schemas.ai import AIRequest, AIResult, AIStreamEvent


class AIProvider(Protocol):
    async def generate(self, request: AIRequest, model: str) -> AIResult: ...

    def stream(self, request: AIRequest, model: str) -> AsyncIterator[AIStreamEvent]: ...
