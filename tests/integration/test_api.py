from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dependencies import CurrentUser, get_current_user
from app.api.v1.models import get_model_manifest_manager
from app.core.config import Settings
from app.main import create_app
from app.services.model_delivery import ModelDeliveryService


class FakeModelSigner:
    def sign_get_object(self, object_key, expiration):
        return f"https://download.example/{object_key}?signed=true"


class FakeModelManifestManager:
    def __init__(self) -> None:
        self.delivery = ModelDeliveryService(FakeModelSigner(), 900)

    async def get_manifest(self, user_id, model_id, client_ip):
        return self.delivery.get_manifest(model_id)


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
    app.dependency_overrides[get_model_manifest_manager] = FakeModelManifestManager

    with TestClient(app) as client:
        response = client.get("/api/v1/models/bge-large-zh-v1.5-fp16/manifest")

    assert response.status_code == 401


def test_model_manifest_returns_only_catalogued_files() -> None:
    app = create_app(Settings(deepseek_api_key=SecretStr("")))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=uuid4(), session_id=uuid4()
    )
    app.dependency_overrides[get_model_manifest_manager] = FakeModelManifestManager

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
    app.dependency_overrides[get_model_manifest_manager] = FakeModelManifestManager

    with TestClient(app) as client:
        response = client.get("/api/v1/models/not-allowed/manifest")

    assert response.status_code == 404


def test_model_manifest_limits_repeated_requests_by_ip() -> None:
    app = create_app(
        Settings(
            deepseek_api_key=SecretStr(""),
            model_manifest_ip_rate_per_minute=1,
            model_manifest_ip_burst=0,
        )
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=uuid4(), session_id=uuid4()
    )
    app.dependency_overrides[get_model_manifest_manager] = FakeModelManifestManager

    with TestClient(app) as client:
        first = client.get("/api/v1/models/bge-large-zh-v1.5-fp16/manifest")
        limited = client.get("/api/v1/models/bge-large-zh-v1.5-fp16/manifest")

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.json()["detail"]["code"] == "MODEL_DOWNLOAD_RATE_LIMITED"
