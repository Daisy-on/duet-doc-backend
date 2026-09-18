from starlette.requests import Request

from app.services.rate_limit import InMemoryTokenBucket, RateLimitExceeded, get_client_ip


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


async def test_token_bucket_allows_burst_then_recovers() -> None:
    clock = Clock()
    limiter = InMemoryTokenBucket(rate=2, period_seconds=60, burst=1, clock=clock)

    await limiter.check("client")
    await limiter.check("client")
    await limiter.check("client")

    try:
        await limiter.check("client")
    except RateLimitExceeded as exc:
        assert exc.retry_after_seconds == 30
    else:
        raise AssertionError("Expected the fourth request to be limited")

    clock.value = 30
    await limiter.check("client")


def request_with_ip(ip: str, forwarded_for: str | None = None) -> Request:
    headers = [] if forwarded_for is None else [(b"x-forwarded-for", forwarded_for.encode())]
    return Request({"type": "http", "client": (ip, 1234), "headers": headers})


def test_client_ip_ignores_untrusted_forwarded_header() -> None:
    request = request_with_ip("192.0.2.10", "203.0.113.7")
    assert get_client_ip(request, trust_proxy_headers=False) == "192.0.2.10"


def test_client_ip_uses_trusted_forwarded_header() -> None:
    request = request_with_ip("127.0.0.1", "203.0.113.7, 127.0.0.1")
    assert get_client_ip(request, trust_proxy_headers=True) == "203.0.113.7"
