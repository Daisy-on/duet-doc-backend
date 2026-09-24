import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.dashscope_vision import DashScopeVisionProvider


@pytest.mark.asyncio
async def test_image_description_uses_signed_url_and_configured_model():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer test-key"
        assert body["model"] == "qwen3-vl-flash"
        assert body["messages"][0]["content"][0]["image_url"]["url"] == "https://oss.test/signed"
        prompt = body["messages"][0]["content"][1]["text"]
        assert "客观描述" in prompt
        assert "代码或报错" in prompt
        assert "不套固定分类" in prompt
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "  图中是一个图表。  "}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DashScopeVisionProvider(
            Settings(dashscope_api_key=SecretStr("test-key")), client
        )
        assert await provider.describe_image("https://oss.test/signed") == "图中是一个图表。"


@pytest.mark.asyncio
async def test_empty_image_description_is_rejected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})
        )
    ) as client:
        provider = DashScopeVisionProvider(
            Settings(dashscope_api_key=SecretStr("test-key")), client
        )
        with pytest.raises(ValueError, match="empty image description"):
            await provider.describe_image("https://oss.test/signed")
