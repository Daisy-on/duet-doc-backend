"""Bind password login to the existing development user without moving its data."""

import argparse
import asyncio
from getpass import getpass
from uuid import UUID

from pydantic import ValidationError

from app.core.config import get_settings
from app.database import create_database
from app.schemas.auth import RegisterRequest
from app.services.auth_service import bind_password_identity


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    if settings.app_env != "development" or not settings.dev_auth_enabled:
        raise RuntimeError("Binding requires development mode and DEV_AUTH_ENABLED=true")

    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    try:
        validated = RegisterRequest(
            username=args.username,
            password=password,
            display_name=args.display_name,
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    engine, sessions = create_database(settings)
    try:
        async with sessions() as session:
            await bind_password_identity(
                session,
                UUID(settings.dev_user_id),
                validated.username,
                validated.password,
                validated.display_name,
            )
    finally:
        await engine.dispose()
    print(f"Password login bound to development user {settings.dev_user_id}.")


if __name__ == "__main__":
    asyncio.run(main())
