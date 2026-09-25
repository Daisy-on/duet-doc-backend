import httpx
import pytest

from app.bge_real_document_probe import compare, fetch_cloud_vectors, local_report, rank, validate


def vector(index: int) -> list[float]:
    values = [0.0] * 1024
    values[index] = 1.0
    return values


def fixture() -> dict:
    return {
        "version": 2,
        "localModel": "bge-large-zh-v1.5-fp16",
        "sourceId": "doc-1",
        "totalChunkCount": 2,
        "passages": [
            {"id": "a", "text": "苹果", "embedding": vector(0)},
            {"id": "b", "text": "香蕉", "embedding": vector(1)},
        ],
        "queries": [
            {"id": "q1", "input": "苹果？", "expectedPassageIds": ["a"], "embedding": vector(0)}
        ],
    }


def test_compare_all_four_rankings() -> None:
    data = fixture()
    report = compare(data, [vector(0), vector(1), vector(0)])
    assert "Hit@1 1/1" in report
    assert report.count("MRR 1.000") == 4
    assert rank(vector(1), data["passages"], {"a"}) == 2


def test_validate_rejects_missing_gold_chunk() -> None:
    data = fixture()
    data["queries"][0]["expectedPassageIds"] = ["missing"]
    with pytest.raises(ValueError, match="预期分块"):
        validate(data)


def test_full_document_accepts_multiple_gold_chunks() -> None:
    data = fixture()
    data["version"] = 3
    data["queries"][0]["expectedPassageIds"] = ["a", "b"]
    passages, queries = validate(data)
    assert len(passages) == data["totalChunkCount"]
    assert rank(vector(1), passages, set(queries[0]["expectedPassageIds"])) == 1
    assert "Hit@1 1/1" in local_report(data)


def test_full_document_rejects_partial_export() -> None:
    data = fixture()
    data["version"] = 3
    data["totalChunkCount"] = 3
    with pytest.raises(ValueError, match="全文样本"):
        validate(data)


def test_cloud_response_is_ordered_by_input_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/embeddings"
        assert request.read().decode().count("BAAI/bge-large-zh-v1.5") == 1
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": vector(1)},
                    {"index": 0, "embedding": vector(0)},
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer test-key"}
    ) as client:
        result = fetch_cloud_vectors(client, ["第一段", "第二段"])

    assert result[0] == vector(0)
    assert result[1] == vector(1)


def test_cloud_response_rejects_incomplete_batch() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": vector(0)}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="incomplete embedding batch"):
            fetch_cloud_vectors(client, ["第一段", "第二段"])
