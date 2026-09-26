from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.rag_worker import fail_job, is_retryable_job_error


def test_retries_transient_provider_and_network_errors() -> None:
    request = httpx.Request("POST", "https://example.com/embeddings")
    for status in (408, 429, 500, 503):
        response = httpx.Response(status, request=request)
        assert is_retryable_job_error(
            httpx.HTTPStatusError("failed", request=request, response=response)
        )
    assert is_retryable_job_error(httpx.ConnectError("offline", request=request))
    assert is_retryable_job_error(httpx.ReadTimeout("timed out", request=request))


def test_does_not_retry_permanent_or_invalid_input_errors() -> None:
    request = httpx.Request("POST", "https://example.com/embeddings")
    for status in (400, 401, 403, 422):
        response = httpx.Response(status, request=request)
        assert not is_retryable_job_error(
            httpx.HTTPStatusError("failed", request=request, response=response)
        )
    assert not is_retryable_job_error(ValueError("invalid input"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status,should_retry", [(403, False), (503, True)])
async def test_failed_job_is_requeued_only_for_transient_errors(status, should_retry) -> None:
    request = httpx.Request("POST", "https://example.com/embeddings")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("provider failed", request=request, response=response)
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    settings = MagicMock(rag_worker_max_attempts=3)
    job = {"id": "job-1", "run_id": "run-1", "attempts": 0}

    with patch("app.rag_worker.finish_job", new=AsyncMock()) as finish:
        await fail_job(sessions, settings, job, error)

    if should_retry:
        assert "status='pending'" in session.execute.await_args.args[0].text
        finish.assert_not_awaited()
    else:
        finish.assert_awaited_once_with(session, job, "error", str(error))
    session.commit.assert_awaited_once()
