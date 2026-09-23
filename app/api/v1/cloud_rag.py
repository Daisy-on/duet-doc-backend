from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import AuthenticatedUser
from app.api.v1.sync import get_session
from app.schemas.cloud_rag import (
    CloudRagCoverage,
    CloudRagPlan,
    CloudRagRunCreated,
    CloudRagRunRequest,
    CloudRagRunStatus,
)
from app.services.cloud_rag import (
    cloud_rag_coverage,
    cloud_rag_plan,
    cloud_rag_run_status,
    create_cloud_rag_run,
)

router = APIRouter(prefix="/rag/workspaces/{workspace_id}/cloud-index", tags=["rag"])
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]


@router.get("/coverage", response_model=CloudRagCoverage)
async def coverage(workspace_id: UUID, current_user: AuthenticatedUser, session: Session):
    return await cloud_rag_coverage(session, current_user.user_id, workspace_id)


@router.get("/plan", response_model=CloudRagPlan)
async def plan(
    workspace_id: UUID,
    current_user: AuthenticatedUser,
    session: Session,
    include_text: bool = True,
    include_images: bool = False,
):
    return await cloud_rag_plan(
        session, current_user.user_id, workspace_id, include_text, include_images
    )


@router.post("/runs", response_model=CloudRagRunCreated)
async def create_run(
    workspace_id: UUID,
    request: CloudRagRunRequest,
    current_user: AuthenticatedUser,
    session: Session,
):
    return await create_cloud_rag_run(
        session, current_user.user_id, workspace_id, request.include_text, request.include_images
    )


@router.get("/runs/{run_id}", response_model=CloudRagRunStatus)
async def run_status(
    workspace_id: UUID, run_id: UUID, current_user: AuthenticatedUser, session: Session
):
    return await cloud_rag_run_status(session, current_user.user_id, workspace_id, run_id)
