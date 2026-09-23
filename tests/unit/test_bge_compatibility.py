import io
import json
import sys

import httpx
import pytest

from app import bge_compatibility
from app.bge_compatibility import DIMENSION, compare, fetch_cloud_vectors


def vector(first: float, second: float) -> list[float]:
    return [first, second, *([0.0] * (DIMENSION - 2))]


def test_cloud_response_is_ordered_by_input_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/embeddings"
        assert request.read().decode().count("BAAI/bge-large-zh-v1.5") == 1
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": vector(0, 2)},
                    {"index": 0, "embedding": vector(3, 0)},
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer test-key"}
    ) as client:
        result = fetch_cloud_vectors(client, ["第一段", "第二段"])

    assert result[0][:2] == [1.0, 0.0]
    assert result[1][:2] == [0.0, 1.0]


def test_compare_reports_cross_retrieval_loss() -> None:
    fixture = {
        "localModel": "bge-large-zh-v1.5-q4f16",
        "passages": [
            {"id": "first", "embedding": vector(1, 0)},
            {"id": "second", "embedding": vector(0, 1)},
            {"id": "third", "embedding": vector(-1, 0)},
            {"id": "fourth", "embedding": vector(0, -1)},
        ],
        "queries": [{"id": "question", "expectedPassageId": "first", "embedding": vector(1, 0)}],
    }
    cloud_vectors = [
        vector(-1, 0),
        vector(0, 1),
        vector(1, 0),
        vector(0, -1),
        vector(-1, 0),
    ]

    report = compare(fixture, cloud_vectors)

    assert "本地→本地：Hit@1 1/1" in report
    assert "云端→本地：Hit@1 0/1" in report
    assert "提示：交叉检索" in report


@pytest.mark.parametrize("precision", ["q4f16", "fp16"])
def test_main_accepts_both_local_precisions(monkeypatch, capsys, precision: str) -> None:
    fixture = {
        "version": 1,
        "localModel": f"bge-large-zh-v1.5-{precision}",
        "passages": [{"id": "first", "text": "第一段", "embedding": vector(1, 0)}],
        "queries": [
            {
                "id": "question",
                "text": "第一段？",
                "input": "为这个句子生成表示以用于检索相关文章：第一段？",
                "expectedPassageId": "first",
                "embedding": vector(1, 0),
            }
        ],
    }
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fixture)))
    monkeypatch.setattr(
        bge_compatibility,
        "fetch_cloud_vectors",
        lambda client, inputs: [vector(1, 0) for _ in inputs],
    )

    bge_compatibility.main()

    assert "本地→云端：Hit@1 1/1" in capsys.readouterr().out
