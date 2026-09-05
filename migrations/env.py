import asyncio

from alembic import context

from app.core.config import get_settings
from app.database import create_database


def run(connection):
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine, _ = create_database(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run)
    finally:
        await engine.dispose()


asyncio.run(main())
