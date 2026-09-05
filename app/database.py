from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings


def create_database(settings: Settings):
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
