import logging

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.siliconflow_embedding import SiliconFlowEmbeddingProvider


@pytest.mark.asyncio
async def test_bge_embedding_is_normalized_and_1024_dimensional():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.read().decode().count("BAAI/bge-large-zh-v1.5") == 1
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [3.0, 4.0] + [0.0] * 1022}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SiliconFlowEmbeddingProvider(
            Settings(
                siliconflow_api_key=SecretStr("test-key"),
                rag_embedding_model="BAAI/bge-large-zh-v1.5",
                rag_embedding_dimension=1024,
            ),
            client,
        )
        vector = await provider.embed_text("测试文本")

    assert len(vector) == 1024
    assert vector[:2] == pytest.approx([0.6, 0.8])


@pytest.mark.asyncio
async def test_old_embedding_model_cannot_run_against_bge_index():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="RAG_EMBEDDING_MODEL"):
            SiliconFlowEmbeddingProvider(
                Settings(
                    siliconflow_api_key=SecretStr("test-key"),
                    rag_embedding_model="qwen3-vl-embedding",
                ),
                client,
            )


@pytest.mark.asyncio
async def test_embedding_error_logs_provider_message_without_input_or_key(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "InvalidInput", "message": "Too long: private text test-key"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SiliconFlowEmbeddingProvider(
            Settings(
                siliconflow_api_key=SecretStr("test-key"),
                rag_embedding_model="BAAI/bge-large-zh-v1.5",
            ),
            client,
        )
        with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
            await provider.embed_text("private text")

    assert "status=400 code=InvalidInput" in caplog.text
    assert "Too long:" in caplog.text
    assert "private text" not in caplog.text
    assert "test-key" not in caplog.text
