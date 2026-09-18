import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
from time import monotonic

from fastapi import Request


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after_seconds = max(1, retry_after_seconds)


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class InMemoryTokenBucket:
    def __init__(
        self,
        rate: int,
        period_seconds: int,
        burst: int = 0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._capacity = float(rate + burst)
        self._refill_per_second = rate / period_seconds
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()
        self._checks = 0

    async def check(self, key: str) -> None:
        now = self._clock()
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, updated_at=now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(
                    self._capacity,
                    bucket.tokens + elapsed * self._refill_per_second,
                )
                bucket.updated_at = now

            if bucket.tokens < 1:
                retry_after = math.ceil((1 - bucket.tokens) / self._refill_per_second)
                raise RateLimitExceeded(retry_after)

            bucket.tokens -= 1
            self._checks += 1
            if self._checks % 256 == 0:
                self._remove_idle_buckets(now)

    def _remove_idle_buckets(self, now: float) -> None:
        idle_seconds = self._capacity / self._refill_per_second
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at >= idle_seconds
        ]
        for key in stale:
            self._buckets.pop(key, None)


def get_client_ip(request: Request, trust_proxy_headers: bool) -> str:
    direct_ip = request.client.host if request.client else "127.0.0.1"
    candidate = direct_ip
    if trust_proxy_headers:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            candidate = forwarded_for.split(",", 1)[0].strip()
        else:
            candidate = request.headers.get("X-Real-IP", direct_ip).strip()

    try:
        return str(ip_address(candidate))
    except ValueError:
        return direct_ip
