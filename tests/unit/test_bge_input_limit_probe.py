import json

import httpx

from app.bge_input_limit_probe import probe


def test_probe_prints_status_without_embedding_or_request_text(capsys):
    inputs = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs.append(json.loads(request.content)["input"][0])
        return httpx.Response(
            400,
            json={"code": 20015, "message": "The parameter is invalid.", "data": None},
            headers={"x-siliconcloud-trace-id": "trace-1"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = probe(
            client,
            "https://example.com/v1/embeddings",
            "BAAI/bge-large-zh-v1.5",
            "长文本",
            "hello " * 700,
        )

    output = capsys.readouterr().out
    assert response.status_code == 400
    assert inputs == ["hello " * 700]
    assert "HTTP 400" in output
    assert "20015" in output
    assert "trace-1" in output
    assert "hello" not in output
