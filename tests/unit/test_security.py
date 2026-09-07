from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_and_update_password,
    verify_password,
)


def test_password_hash_uses_argon2id_and_verifies() -> None:
    encoded = hash_password("correct-horse-battery-staple")

    assert encoded.startswith("$argon2id$")
    assert "correct-horse-battery-staple" not in encoded
    assert verify_password("correct-horse-battery-staple", encoded)
    assert not verify_password("wrong-password", encoded)


def test_current_password_hash_does_not_need_rehash() -> None:
    encoded = hash_password("correct-horse-battery-staple")

    verified, updated_hash = verify_and_update_password("correct-horse-battery-staple", encoded)

    assert verified
    assert updated_hash is None


def test_access_token_round_trip_and_rejects_wrong_audience() -> None:
    user_id, session_id = uuid4(), uuid4()
    settings = Settings()
    token, expires_in = create_access_token(user_id, session_id, settings)

    assert expires_in == 15 * 60
    assert decode_access_token(token, settings) == (user_id, session_id)

    with pytest.raises(ValueError):
        decode_access_token(token, Settings(auth_jwt_audience="another-app"))
