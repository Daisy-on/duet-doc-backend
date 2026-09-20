import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.dashscope_embedding import DashScopeEmbeddingProvider


@pytest.mark.asyncio
@respx.mock
async def test_embeds_text_with_configured_dimension():
    settings = Settings(dashscope_api_key=SecretStr("test-key"))
    route = respx.post(settings.rag_embedding_base_url).mock(
        return_value=httpx.Response(
            200,
            json={"output": {"embeddings": [{"embedding": [0.1] * 768}]}},
        )
    )
    async with httpx.AsyncClient() as client:
        provider = DashScopeEmbeddingProvider(settings, client)
        embedding = await provider.embed_text("测试")

    assert route.called
    assert len(embedding) == 768
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-key"
