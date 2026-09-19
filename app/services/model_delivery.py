from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import alibabacloud_oss_v2 as oss

from app.core.config import Settings
from app.schemas.models import ModelManifest, ModelManifestFile
from app.services.oss_client import create_oss_client


class ModelNotFoundError(Exception):
    pass


class ModelSigningError(Exception):
    pass


@dataclass(frozen=True)
class ModelFileSpec:
    path: str
    size_bytes: int


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    directory: str
    version: str
    precision: str
    files: tuple[ModelFileSpec, ...]


MODEL_CATALOG = {
    "multilingual-e5-base-fp16": ModelSpec(
        model_id="multilingual-e5-base-fp16",
        directory="multilingual-e5-base",
        version="v1",
        precision="fp16",
        files=(
            ModelFileSpec("config.json", 656),
            ModelFileSpec("configuration.json", 77),
            ModelFileSpec("special_tokens_map.json", 964),
            ModelFileSpec("tokenizer_config.json", 1_177),
            ModelFileSpec("tokenizer.json", 17_082_734),
            ModelFileSpec("onnx/model_fp16.onnx", 555_234_024),
        ),
    ),
    "qwen3.5-0.8b-opt-q4f16": ModelSpec(
        model_id="qwen3.5-0.8b-opt-q4f16",
        directory="qwen3.5-0.8b-opt",
        version="v1",
        precision="q4f16",
        files=(
            ModelFileSpec("chat_template.jinja", 7_755),
            ModelFileSpec("config.json", 2_849),
            ModelFileSpec("generation_config.json", 248),
            ModelFileSpec("preprocessor_config.json", 336),
            ModelFileSpec("processor_config.json", 1_300),
            ModelFileSpec("tokenizer_config.json", 9_161),
            ModelFileSpec("tokenizer.json", 19_226_111),
            ModelFileSpec("onnx/decoder_model_merged_q4f16.onnx", 697_833),
            ModelFileSpec("onnx/decoder_model_merged_q4f16.onnx_data", 435_777_536),
            ModelFileSpec("onnx/embed_tokens_q4f16.onnx", 1_064),
            ModelFileSpec("onnx/embed_tokens_q4f16.onnx_data", 147_005_440),
            ModelFileSpec("onnx/vision_encoder_q4f16.onnx", 213_118),
            ModelFileSpec("onnx/vision_encoder_q4f16.onnx_data", 61_919_744),
        ),
    ),
}


class ObjectSigner(Protocol):
    def sign_get_object(self, object_key: str, expiration: datetime) -> str: ...


class OSSObjectSigner:
    def __init__(self, settings: Settings) -> None:
        if not settings.oss_bucket or not settings.oss_ecs_role_name:
            raise ValueError("OSS model delivery is not configured")

        self._bucket = settings.oss_bucket
        self._client = create_oss_client(settings)

    def sign_get_object(self, object_key: str, expiration: datetime) -> str:
        try:
            result = self._client.presign(
                oss.GetObjectRequest(bucket=self._bucket, key=object_key),
                expiration=expiration,
            )
        except Exception as exc:
            raise ModelSigningError("Failed to sign OSS model object") from exc
        if not result.url:
            raise ModelSigningError("OSS SDK returned an empty signed URL")
        return result.url


class ModelDeliveryService:
    def __init__(self, signer: ObjectSigner, ttl_seconds: int) -> None:
        self._signer = signer
        self._ttl = timedelta(seconds=ttl_seconds)

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    def validate_model_id(self, model_id: str) -> None:
        if model_id not in MODEL_CATALOG:
            raise ModelNotFoundError(model_id)

    def get_manifest(self, model_id: str, now: datetime | None = None) -> ModelManifest:
        spec = MODEL_CATALOG.get(model_id)
        if spec is None:
            raise ModelNotFoundError(model_id)

        expires_at = (now or datetime.now(UTC)) + self._ttl
        files = [
            ModelManifestFile(
                path=file.path,
                size_bytes=file.size_bytes,
                url=self._signer.sign_get_object(
                    f"models/{spec.version}/{spec.directory}/{file.path}",
                    expires_at,
                ),
            )
            for file in spec.files
        ]
        return ModelManifest(
            model_id=spec.model_id,
            version=spec.version,
            precision=spec.precision,
            total_size_bytes=sum(file.size_bytes for file in spec.files),
            expires_at=expires_at,
            files=files,
        )


def create_model_delivery_service(settings: Settings) -> ModelDeliveryService | None:
    if not settings.oss_bucket or not settings.oss_ecs_role_name:
        return None
    return ModelDeliveryService(
        signer=OSSObjectSigner(settings),
        ttl_seconds=settings.model_download_url_ttl_seconds,
    )
