import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.models import ModelManifest
from app.services.model_delivery import ModelDeliveryService


class ModelManifestRateLimitError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Model manifest rate limit exceeded")
        self.retry_after_seconds = max(1, retry_after_seconds)


class ModelGrantIssuer(Protocol):
    async def issue(
        self,
        user_id: UUID,
        model_id: str,
        client_ip: str,
        expires_at: datetime,
        factory: Callable[[], Awaitable[ModelManifest]],
    ) -> ModelManifest: ...


def calculate_retry_after(
    issued_at: list[datetime],
    now: datetime,
    hourly_limit: int,
    daily_limit: int,
) -> int | None:
    hour_start = now - timedelta(hours=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hourly = sorted(timestamp for timestamp in issued_at if timestamp >= hour_start)
    daily = [timestamp for timestamp in issued_at if timestamp >= day_start]
    retry_at: list[datetime] = []
    if len(hourly) >= hourly_limit:
        retry_at.append(hourly[-hourly_limit] + timedelta(hours=1))
    if len(daily) >= daily_limit:
        retry_at.append(day_start + timedelta(days=1))
    if not retry_at:
        return None
    return max(1, math.ceil((max(retry_at) - now).total_seconds()))


class PostgresModelGrantIssuer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        hourly_limit: int,
        daily_limit: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._hourly_limit = hourly_limit
        self._daily_limit = daily_limit
        self._clock = clock

    async def issue(
        self,
        user_id: UUID,
        model_id: str,
        client_ip: str,
        expires_at: datetime,
        factory: Callable[[], Awaitable[ModelManifest]],
    ) -> ModelManifest:
        now = self._clock()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = min(day_start, now - timedelta(hours=1))
        lock_material = f"model-manifest:{user_id}:{model_id}".encode()
        lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)

        async with self._sessions() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            issued_at = list(
                (
                    await session.execute(
                        text(
                            "SELECT issued_at FROM model_download_grants "
                            "WHERE user_id=:user_id AND model_id=:model_id "
                            "AND issued_at>=:since ORDER BY issued_at"
                        ),
                        {"user_id": user_id, "model_id": model_id, "since": since},
                    )
                ).scalars()
            )
            retry_after = calculate_retry_after(
                issued_at,
                now,
                self._hourly_limit,
                self._daily_limit,
            )
            if retry_after is not None:
                raise ModelManifestRateLimitError(retry_after)

            manifest = await factory()
            await session.execute(
                text(
                    "INSERT INTO model_download_grants "
                    "(id,user_id,model_id,client_ip,issued_at,expires_at) "
                    "VALUES (:id,:user_id,:model_id,:client_ip,:issued_at,:expires_at)"
                ),
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "model_id": model_id,
                    "client_ip": client_ip,
                    "issued_at": now,
                    "expires_at": expires_at,
                },
            )
            return manifest


@dataclass(frozen=True)
class _CachedManifest:
    manifest: ModelManifest
    reusable_until: datetime


class ModelManifestManager:
    def __init__(
        self,
        delivery: ModelDeliveryService,
        grant_issuer: ModelGrantIssuer,
        cache_safety_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._delivery = delivery
        self._grant_issuer = grant_issuer
        self._cache_safety = timedelta(seconds=cache_safety_seconds)
        self._clock = clock
        self._cache: dict[tuple[UUID, str], _CachedManifest] = {}
        self._locks: dict[tuple[UUID, str], asyncio.Lock] = {}

    async def get_manifest(
        self,
        user_id: UUID,
        model_id: str,
        client_ip: str,
    ) -> ModelManifest:
        self._delivery.validate_model_id(model_id)
        key = (user_id, model_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = self._clock()
            cached = self._cache.get(key)
            if cached and cached.reusable_until > now:
                return cached.manifest
            self._cache.pop(key, None)

            expires_at = now + self._delivery.ttl

            async def create_manifest() -> ModelManifest:
                return await asyncio.to_thread(self._delivery.get_manifest, model_id, now)

            manifest = await self._grant_issuer.issue(
                user_id,
                model_id,
                client_ip,
                expires_at,
                create_manifest,
            )
            self._cache[key] = _CachedManifest(
                manifest=manifest,
                reusable_until=manifest.expires_at - self._cache_safety,
            )
            return manifest
