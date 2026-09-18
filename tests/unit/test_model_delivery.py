from datetime import datetime

import pytest

from app.services.model_delivery import (
    MODEL_CATALOG,
    ModelDeliveryService,
    ModelNotFoundError,
)


class FakeSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    def sign_get_object(self, object_key: str, expiration: datetime) -> str:
        self.calls.append((object_key, expiration))
        return f"https://download.example/{object_key}?signed=true"


def test_manifest_only_signs_catalogued_model_files() -> None:
    signer = FakeSigner()
    service = ModelDeliveryService(signer=signer, ttl_seconds=900)

    manifest = service.get_manifest("multilingual-e5-base-fp16")

    spec = MODEL_CATALOG["multilingual-e5-base-fp16"]
    assert manifest.model_id == spec.model_id
    assert manifest.total_size_bytes == sum(file.size_bytes for file in spec.files)
    assert [file.path for file in manifest.files] == [file.path for file in spec.files]
    assert [key for key, _ in signer.calls] == [
        f"models/v1/multilingual-e5-base/{file.path}" for file in spec.files
    ]
    assert len({expiration for _, expiration in signer.calls}) == 1


def test_unknown_model_is_rejected_before_signing() -> None:
    signer = FakeSigner()
    service = ModelDeliveryService(signer=signer, ttl_seconds=900)

    with pytest.raises(ModelNotFoundError):
        service.get_manifest("models/v1/arbitrary-object")

    assert signer.calls == []
