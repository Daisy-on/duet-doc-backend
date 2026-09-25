"""Compare local and cloud BGE retrieval on sampled real document chunks."""

import argparse
import json
import os
import sys
from statistics import median

import httpx

from app.bge_compatibility import MODEL, fetch_cloud_vectors, normalized, similarity

BATCH_SIZE = 8


def validate(fixture: dict) -> tuple[list[dict], list[dict]]:
    if fixture.get("version") != 2 or fixture.get("localModel") != "bge-large-zh-v1.5-fp16":
        raise ValueError("需要真实文档 FP16 对照样本（version 2）。")
    passages, queries = fixture.get("passages"), fixture.get("queries")
    if not isinstance(passages, list) or not 1 <= len(passages) <= 64:
        raise ValueError("分块数量应为 1-64。")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 20:
        raise ValueError("问题数量应为 1-20。")
    ids = {row["id"] for row in passages}
    if len(ids) != len(passages):
        raise ValueError("分块 ID 重复。")
    for row in passages:
        if not isinstance(row["text"], str) or not row["text"].strip():
            raise ValueError("存在空分块。")
        normalized(row["embedding"])
    for row in queries:
        if not isinstance(row["input"], str) or not row["input"].strip():
            raise ValueError("存在空问题。")
        if not row["expectedPassageIds"] or not set(row["expectedPassageIds"]) <= ids:
            raise ValueError("问题的预期分块不在样本中。")
        normalized(row["embedding"])
    return passages, queries


def rank(query: list[float], passages: list[dict], expected: set[str]) -> int | None:
    ordered = sorted(passages, key=lambda row: similarity(query, row["embedding"]), reverse=True)
    return next((index for index, row in enumerate(ordered, 1) if row["id"] in expected), None)


def compare(fixture: dict, cloud_vectors: list[list[float]]) -> str:
    passages, queries = validate(fixture)
    local_passages = [
        {"id": row["id"], "embedding": normalized(row["embedding"])} for row in passages
    ]
    cloud_passages = [
        {"id": row["id"], "embedding": vector}
        for row, vector in zip(passages, cloud_vectors[: len(passages)], strict=True)
    ]
    local_queries = [normalized(row["embedding"]) for row in queries]
    cloud_queries = cloud_vectors[len(passages) :]
    agreement = [
        similarity(local["embedding"], cloud["embedding"])
        for local, cloud in zip(local_passages, cloud_passages, strict=True)
    ] + [
        similarity(local, cloud) for local, cloud in zip(local_queries, cloud_queries, strict=True)
    ]
    labels = ("本地→本地", "云端→云端", "云端→本地", "本地→云端")
    rankings: dict[str, list[int | None]] = {label: [] for label in labels}
    lines = [
        f"文档：{fixture['sourceId']}，总块 {fixture['totalChunkCount']}，"
        f"抽样 {len(passages)}，问题 {len(queries)}",
        f"模型：本地 FP16 / 云端 {MODEL}",
        f"同输入余弦相似度：最小 {min(agreement):.4f}，中位数 {median(agreement):.4f}",
        "问题 | 本地→本地 | 云端→云端 | 云端→本地 | 本地→云端",
    ]
    for row, local_query, cloud_query in zip(queries, local_queries, cloud_queries, strict=True):
        expected = set(row["expectedPassageIds"])
        values = (
            rank(local_query, local_passages, expected),
            rank(cloud_query, cloud_passages, expected),
            rank(cloud_query, local_passages, expected),
            rank(local_query, cloud_passages, expected),
        )
        for label, value in zip(labels, values, strict=True):
            rankings[label].append(value)
        lines.append(f"{row['id']} | " + " | ".join(str(value or "未命中") for value in values))
    for label, values in rankings.items():
        denominator = len(values)
        lines.append(
            f"{label}：Hit@1 {sum(value == 1 for value in values)}/{denominator}，"
            f"Hit@3 {sum(value is not None and value <= 3 for value in values)}/{denominator}，"
            f"Hit@5 {sum(value is not None and value <= 5 for value in values)}/{denominator}，"
            f"MRR {sum(1 / value for value in values if value is not None) / denominator:.3f}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", help="浏览器导出的 JSON 文件路径")
    parser.add_argument(
        "--confirm-cloud-calls", action="store_true", help="确认调用硅基流动付费接口"
    )
    args = parser.parse_args()
    with open(args.fixture, encoding="utf-8") as stream:
        fixture = json.load(stream)
    passages, queries = validate(fixture)
    inputs = [row["text"] for row in passages] + [row["input"] for row in queries]
    batches = [inputs[index : index + BATCH_SIZE] for index in range(0, len(inputs), BATCH_SIZE)]
    print(
        f"将处理 {len(passages)} 个分块、{len(queries)} 条问题；"
        f"云端 {len(inputs)} 条输入、{len(batches)} 次请求。"
    )
    if not args.confirm_cloud_calls:
        print("当前仅预览，没有调用云端。确认费用后加 --confirm-cloud-calls 执行。")
        return
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise SystemExit("缺少 SILICONFLOW_API_KEY。")
    cloud_vectors: list[list[float]] = []
    with httpx.Client(timeout=60, headers={"Authorization": f"Bearer {key}"}) as client:
        for batch in batches:
            cloud_vectors.extend(fetch_cloud_vectors(client, batch))
    print(compare(fixture, cloud_vectors))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, httpx.HTTPError) as exc:
        sys.exit(f"对照失败：{exc}")
