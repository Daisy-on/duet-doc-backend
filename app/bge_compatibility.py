"""Compare browser Q4F16 and SiliconFlow BGE vectors without changing stored indexes."""

import json
import math
import os
import sys
from statistics import median

import httpx

MODEL = "BAAI/bge-large-zh-v1.5"
URL = "https://api.siliconflow.cn/v1/embeddings"
DIMENSION = 1024


def normalized(vector: list[float]) -> list[float]:
    if len(vector) != DIMENSION or any(not math.isfinite(value) for value in vector):
        raise ValueError(f"Expected a finite {DIMENSION}-dimensional vector")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("Embedding vector is empty")
    return [value / norm for value in vector]


def similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def fetch_cloud_vectors(client: httpx.Client, inputs: list[str]) -> list[list[float]]:
    response = client.post(URL, json={"model": MODEL, "input": inputs, "encoding_format": "float"})
    response.raise_for_status()
    rows = sorted(response.json()["data"], key=lambda row: row["index"])
    if len(rows) != len(inputs) or [row["index"] for row in rows] != list(range(len(inputs))):
        raise ValueError("SiliconFlow returned an incomplete embedding batch")
    return [normalized(row["embedding"]) for row in rows]


def rank(expected_id: str, query: list[float], passages: list[dict]) -> int | None:
    ordered = sorted(
        passages,
        key=lambda passage: similarity(query, passage["embedding"]),
        reverse=True,
    )
    return next((index for index, row in enumerate(ordered, 1) if row["id"] == expected_id), None)


def compare(fixture: dict, cloud_vectors: list[list[float]]) -> str:
    passages = fixture["passages"]
    queries = fixture["queries"]
    local_passages = [
        {"id": row["id"], "embedding": normalized(row["embedding"])} for row in passages
    ]
    local_queries = [normalized(row["embedding"]) for row in queries]
    cloud_passages = [
        {"id": row["id"], "embedding": vector}
        for row, vector in zip(passages, cloud_vectors[: len(passages)], strict=True)
    ]
    cloud_queries = cloud_vectors[len(passages) :]
    pairs = list(zip(local_passages, cloud_passages, strict=True))
    agreements = [similarity(local["embedding"], cloud["embedding"]) for local, cloud in pairs]
    agreements.extend(
        similarity(local, cloud) for local, cloud in zip(local_queries, cloud_queries, strict=True)
    )

    labels = ("本地→本地", "云端→云端", "云端→本地", "本地→云端")
    rankings: dict[str, list[int | None]] = {label: [] for label in labels}
    lines = [
        f"相同输入的向量余弦相似度：最小 {min(agreements):.4f}，中位数 {median(agreements):.4f}",
        "问题 | 本地→本地 | 云端→云端 | 云端→本地 | 本地→云端",
    ]
    for row, local_query, cloud_query in zip(queries, local_queries, cloud_queries, strict=True):
        expected = row["expectedPassageId"]
        values = (
            rank(expected, local_query, local_passages),
            rank(expected, cloud_query, cloud_passages),
            rank(expected, cloud_query, local_passages),
            rank(expected, local_query, cloud_passages),
        )
        for label, value in zip(labels, values, strict=True):
            rankings[label].append(value)
        result = " | ".join(str(value or "未命中") for value in values)
        lines.append(f"{row['id']} ({expected}) | {result}")

    for label in labels:
        values = rankings[label]
        hit_at_1 = sum(value == 1 for value in values)
        hit_at_3 = sum(value is not None and value <= 3 for value in values)
        lines.append(f"{label}：Hit@1 {hit_at_1}/{len(values)}，Hit@3 {hit_at_3}/{len(values)}")
    baseline = sum(value is not None and value <= 3 for value in rankings[labels[0]])
    cross = [
        sum(value is not None and value <= 3 for value in rankings[label]) for label in labels[2:]
    ]
    if min(cross) < baseline or median(agreements) < 0.95:
        lines.append("提示：交叉检索或向量一致性低于基线，请检查输入、截断和模型实现。")
    else:
        lines.append("交叉 Hit@3 未低于本地基线；仍需用真实文档评测后才能确认可共用索引。")
    return "\n".join(lines)


def main() -> None:
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise SystemExit("请先在服务器 .env 设置 SILICONFLOW_API_KEY。")
    fixture = json.load(sys.stdin)
    if fixture.get("version") != 1 or fixture.get("localModel") != "bge-large-zh-v1.5-q4f16":
        raise SystemExit("不支持的本地样本格式或模型。")
    passages = fixture["passages"]
    queries = fixture["queries"]
    if not passages or not queries:
        raise SystemExit("样本必须包含文本和问题。")
    inputs = [row["text"] for row in passages] + [row["input"] for row in queries]
    if any(not isinstance(value, str) or not value.strip() for value in inputs):
        raise SystemExit("样本包含空文本。")
    with httpx.Client(
        timeout=60,
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        cloud_vectors = fetch_cloud_vectors(client, inputs)
    print(compare(fixture, cloud_vectors))


if __name__ == "__main__":
    main()
