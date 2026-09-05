"""Run with python -m app.seed_dev after alembic upgrade head."""

import asyncio
from uuid import UUID

from sqlalchemy import text

from app.core.config import get_settings
from app.database import create_database

WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000002")


async def main():
    settings = get_settings()
    if settings.app_env != "development" or not settings.dev_auth_enabled:
        raise RuntimeError("Development seed requires development and DEV_AUTH_ENABLED=true")
    engine, sessions = create_database(settings)
    try:
        async with sessions() as session, session.begin():
            user_id = UUID(settings.dev_user_id)
            await session.execute(
                text(
                    "INSERT INTO users (id,display_name) VALUES (:id,'Duet Developer') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": user_id},
            )
            await session.execute(
                text(
                    "INSERT INTO workspaces (id,owner_user_id,name) VALUES (:id,:owner,'Personal') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": WORKSPACE_ID, "owner": user_id},
            )
            owner = await session.scalar(
                text("SELECT owner_user_id FROM workspaces WHERE id=:id"), {"id": WORKSPACE_ID}
            )
            if owner != user_id:
                raise RuntimeError("Development workspace belongs to a different user")
        print(f"Development workspace ready: {WORKSPACE_ID}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
