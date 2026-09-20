from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CloudRagCoverage(BaseModel):
    current_sources: int
    ready_sources: int
    stale_sources: int
    missing_sources: int
    has_client_index: bool
    has_cloud_index: bool
    has_any_index: bool


class CloudRagPlan(BaseModel):
    document_count: int
    text_chunk_count: int
    text_character_count: int
    image_count: int
    total_jobs: int


class CloudRagRunCreated(BaseModel):
    run_id: UUID
    status: Literal["pending", "running", "completed"]
    total_jobs: int


class CloudRagRunStatus(BaseModel):
    run_id: UUID
    status: Literal["pending", "running", "completed", "partial", "error"]
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    created_at: datetime
    completed_at: datetime | None
