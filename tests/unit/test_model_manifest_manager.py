from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.services.model_delivery import ModelDeliveryService
from app.services.model_manifest_manager import ModelManifestManager, calculate_retry_after


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class CountingSigner:
    def __init__(self) -> None:
        self.calls = 0

    def sign_get_object(self, object_key, expiration):
        self.calls += 1
        return f"https://download.example/{object_key}?generation={self.calls}"


class CountingGrantIssuer:
    def __init__(self) -> None:
        self.calls = 0

    async def issue(self, user_id, model_id, client_ip, expires_at, factory):
        self.calls += 1
        return await factory()


async def test_manifest_is_reused_without_consuming_another_grant() -> None:
    clock = Clock()
    signer = CountingSigner()
    issuer = CountingGrantIssuer()
    manager = ModelManifestManager(
        ModelDeliveryService(signer, 900),
        issuer,
        cache_safety_seconds=30,
        clock=clock,
    )
    user_id = uuid4()

    first = await manager.get_manifest(user_id, "bge-large-zh-v1.5-fp16", "192.0.2.1")
    second = await manager.get_manifest(user_id, "bge-large-zh-v1.5-fp16", "192.0.2.1")

    assert first == second
    assert issuer.calls == 1
    assert signer.calls == len(first.files)

    clock.value += timedelta(seconds=871)
    third = await manager.get_manifest(user_id, "bge-large-zh-v1.5-fp16", "192.0.2.1")
    assert third != first
    assert issuer.calls == 2


def test_retry_after_combines_hourly_and_daily_limits() -> None:
    now = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)
    issued_at = [
        now - timedelta(minutes=50),
        now - timedelta(minutes=20),
        now - timedelta(minutes=5),
    ]

    assert calculate_retry_after(issued_at, now, hourly_limit=3, daily_limit=8) == 600
    assert calculate_retry_after(issued_at, now, hourly_limit=10, daily_limit=3) == 1800
    assert calculate_retry_after(issued_at, now, hourly_limit=10, daily_limit=8) is None
