from datetime import datetime

from pydantic import BaseModel, ConfigDict


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ModelResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ModelManifestFile(ModelResponse):
    path: str
    size_bytes: int
    url: str


class ModelManifest(ModelResponse):
    model_id: str
    version: str
    precision: str
    total_size_bytes: int
    expires_at: datetime
    files: list[ModelManifestFile]
