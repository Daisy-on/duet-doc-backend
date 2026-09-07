from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import Settings
from app.main import create_app


def test_health() -> None:
    with TestClient(create_app(Settings(deepseek_api_key=SecretStr("")))) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "duet-doc-backend"}
    assert response.headers["X-Request-ID"]


def test_generate_returns_configuration_error_without_api_key() -> None:
    app = create_app(Settings(deepseek_api_key=SecretStr("")))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=uuid4(), session_id=uuid4()
    )
    payload = {
        "task": "chat",
        "messages": [{"role": "user", "content": "你好"}],
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/ai/generate", json=payload)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"


def test_ai_requires_authentication() -> None:
    app = create_app(Settings(deepseek_api_key=SecretStr(""), dev_auth_enabled=False))
    payload = {
        "task": "chat",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/ai/generate", json=payload)

    assert response.status_code == 401
