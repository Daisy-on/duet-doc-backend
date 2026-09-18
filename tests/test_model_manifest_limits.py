from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.database import create_database
from app.schemas.models import ModelManifest
from app.services.model_manifest_manager import (
    ModelManifestRateLimitError,
    PostgresModelGrantIssuer,
)


@pytest.mark.asyncio
async def test_postgres_grant_issuer_persists_and_enforces_user_limit() -> None:
    engine, _ = create_database(Settings())
    connection = await engine.connect()
    transaction = await connection.begin()
    sessions = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    user_id = uuid4()
    now = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    issuer = PostgresModelGrantIssuer(sessions, hourly_limit=1, daily_limit=8, clock=lambda: now)
    manifest = ModelManifest(
        model_id="model",
        version="v1",
        precision="fp16",
        total_size_bytes=1,
        expires_at=now + timedelta(minutes=15),
        files=[],
    )

    async def create_manifest() -> ModelManifest:
        return manifest

    try:
        await connection.execute(
            text("INSERT INTO users (id,display_name) VALUES (:id,'Rate Limit Test')"),
            {"id": user_id},
        )
        issued = await issuer.issue(
            user_id,
            "model",
            "192.0.2.1",
            manifest.expires_at,
            create_manifest,
        )
        assert issued == manifest

        with pytest.raises(ModelManifestRateLimitError):
            await issuer.issue(
                user_id,
                "model",
                "192.0.2.1",
                manifest.expires_at,
                create_manifest,
            )

        count = await connection.scalar(
            text("SELECT count(*) FROM model_download_grants WHERE user_id=:user_id"),
            {"user_id": user_id},
        )
        assert count == 1
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
