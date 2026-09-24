import logging
import math

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class SiliconFlowEmbeddingProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.siliconflow_api_key is None:
            raise ValueError("SILICONFLOW_API_KEY is required by the RAG worker")
        if settings.rag_embedding_model != "BAAI/bge-large-zh-v1.5":
            raise ValueError("RAG_EMBEDDING_MODEL must be BAAI/bge-large-zh-v1.5")
        self.url = settings.rag_embedding_base_url
        self.key = settings.siliconflow_api_key.get_secret_value()
        self.model = settings.rag_embedding_model
        self.dimension = settings.rag_embedding_dimension
        self.client = client

    async def embed_text(self, value: str) -> list[float]:
        response = await self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}"},
            json={"model": self.model, "input": [value], "encoding_format": "float"},
        )
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = {}
            details = body.get("error", body) if isinstance(body, dict) else {}
            if not isinstance(details, dict):
                details = {}
            message = details.get("message")
            if isinstance(message, str):
                message = message.replace(value, "[input omitted]").replace(
                    self.key, "[key omitted]"
                )[:300]
            logger.warning(
                "SiliconFlow embedding failed: status=%s code=%s message=%s",
                response.status_code,
                details.get("code"),
                message,
            )
        response.raise_for_status()
        rows = response.json().get("data", [])
        if len(rows) != 1 or rows[0].get("index") != 0:
            raise ValueError("SiliconFlow returned an invalid embedding batch")
        embedding = rows[0].get("embedding", [])
        if len(embedding) != self.dimension or any(not math.isfinite(v) for v in embedding):
            raise ValueError("SiliconFlow returned an invalid embedding")
        norm = math.sqrt(sum(value * value for value in embedding))
        if norm == 0:
            raise ValueError("SiliconFlow returned an empty embedding")
        return [value / norm for value in embedding]
