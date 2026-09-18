from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dependencies import CurrentUser, get_current_user
from app.api.v1.models import get_model_delivery_service
from app.core.config import Settings
from app.main import create_app
from app.services.model_delivery import ModelDeliveryService


class FakeModelSigner:
    def sign_get_object(self, object_key, expiration):
        return f"https://download.example/{object_key}?signed=true"


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


def test_model_manifest_requires_authentication() -> None:
    app = create_app(Settings(deepseek_api_key=SecretStr(""), dev_auth_enabled=False))
    app.dependency_overrides[get_model_delivery_service] = lambda: ModelDeliveryService(
        FakeModelSigner(), 900
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/models/multilingual-e5-base-fp16/manifest")

    assert response.status_code == 401


def test_model_manifest_returns_only_catalogued_files() -> None:
    app = create_app(Settings(deepseek_api_key=SecretStr("")))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=uuid4(), session_id=uuid4()
    )
    app.dependency_overrides[get_model_delivery_service] = lambda: ModelDeliveryService(
        FakeModelSigner(), 900
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/models/qwen3.5-0.8b-opt-q4f16/manifest")

    assert response.status_code == 200
    body = response.json()
    assert body["modelId"] == "qwen3.5-0.8b-opt-q4f16"
    assert body["precision"] == "q4f16"
    assert body["files"][0]["path"] == "chat_template.jinja"
    assert all(
        file["url"].startswith("https://download.example/models/v1/") for file in body["files"]
    )


def test_model_manifest_rejects_unknown_model() -> None:
    app = create_app(Settings(deepseek_api_key=SecretStr("")))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=uuid4(), session_id=uuid4()
    )
    app.dependency_overrides[get_model_delivery_service] = lambda: ModelDeliveryService(
        FakeModelSigner(), 900
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/models/not-allowed/manifest")

    assert response.status_code == 404
