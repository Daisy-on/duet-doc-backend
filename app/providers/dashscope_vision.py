import httpx

from app.core.config import Settings


class DashScopeVisionProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.dashscope_api_key is None:
            raise ValueError("DASHSCOPE_API_KEY is required for image indexing")
        if settings.rag_vision_model != "qwen3-vl-flash":
            raise ValueError("RAG_VISION_MODEL must be qwen3-vl-flash")
        self.url = settings.rag_vision_base_url
        self.model = settings.rag_vision_model
        self.key = settings.dashscope_api_key.get_secret_value()
        self.client = client

    async def describe_image(self, image_url: str) -> str:
        response = await self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_url}},
                            {
                                "type": "text",
                                "text": (
                                    "客观描述图片中可用于检索的信息。按图片实际内容，保留可辨认的"
                                    "文字、数字、代码或报错；说明图表关系或画面主体。"
                                    "用简短的句子或分行陈述，不套固定分类，不推测看不清的内容。"
                                ),
                            },
                        ],
                    }
                ],
            },
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        description = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(description, str) or not description.strip():
            raise ValueError("DashScope returned an empty image description")
        return description.strip()
