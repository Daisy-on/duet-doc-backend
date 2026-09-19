from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    content_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    size_bytes: int = Field(gt=0)
    md5_hex: str = Field(pattern=r"^[0-9a-f]{32}$")


class MediaState(BaseModel):
    asset_id: UUID
    status: Literal["pending", "ready"]


class UploadResponse(MediaState):
    upload_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None


class MediaAccess(BaseModel):
    asset_id: UUID
    url: str
    expires_at: datetime
