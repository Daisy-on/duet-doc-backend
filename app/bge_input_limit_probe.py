"""Probe SiliconFlow BGE with short and over-limit fixed text inputs."""

import httpx

from app.core.config import Settings


def probe(client: httpx.Client, url: str, model: str, label: str, value: str) -> httpx.Response:
    response = client.post(
        url,
        json={"model": model, "input": [value], "encoding_format": "float"},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    print(f"{label}: HTTP {response.status_code}")
    print(f"  code: {body.get('code')}")
    print(f"  message: {body.get('message')}")
    print(f"  usage: {body.get('usage')}")
    print(f"  trace_id: {response.headers.get('x-siliconcloud-trace-id')}")
    return response


def main() -> None:
    settings = Settings()
    if settings.siliconflow_api_key is None:
        raise SystemExit("请先配置 SILICONFLOW_API_KEY。")
    key = settings.siliconflow_api_key.get_secret_value()
    long_text = " ".join(["hello"] * 700)
    print("长文本：700 个空格分隔的 hello（预计超过 512 tokens）")
    with httpx.Client(
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    ) as client:
        short = probe(
            client,
            settings.rag_embedding_base_url,
            settings.rag_embedding_model,
            "短文本对照",
            "测试图片描述",
        )
        if not short.is_success:
            raise SystemExit("短文本也未成功，停止测试；请先检查模型和密钥配置。")
        probe(
            client,
            settings.rag_embedding_base_url,
            settings.rag_embedding_model,
            "长文本",
            long_text,
        )


if __name__ == "__main__":
    main()
