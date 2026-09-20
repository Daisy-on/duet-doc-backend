import httpx

from app.core.config import Settings


class DashScopeEmbeddingProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.dashscope_api_key is None:
            raise ValueError("DASHSCOPE_API_KEY is required by the RAG worker")
        self.url = settings.rag_embedding_base_url
        self.key = settings.dashscope_api_key.get_secret_value()
        self.model = settings.rag_embedding_model
        self.dimension = settings.rag_embedding_dimension
        self.client = client

    async def embed_text(self, value: str) -> list[float]:
        return await self._embed({"text": value})

    async def embed_image(self, url: str) -> list[float]:
        return await self._embed({"image": url})

    async def _embed(self, content: dict[str, str]) -> list[float]:
        response = await self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "input": {"contents": [content]},
                "parameters": {"dimension": self.dimension},
            },
        )
        response.raise_for_status()
        body = response.json()
        embeddings = body.get("output", {}).get("embeddings", [])
        if not embeddings or len(embeddings[0].get("embedding", [])) != self.dimension:
            raise ValueError("DashScope returned an invalid embedding")
        return embeddings[0]["embedding"]
