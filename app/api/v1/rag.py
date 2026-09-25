from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import AuthenticatedUser
from app.api.v1.sync import get_session
from app.schemas.rag import RagSearchRequest, RagSearchResponse, TextIndexStatus, TextIndexUpload
from app.services.rag_search import search_rag
from app.services.rag_text_indexes import list_text_index_statuses, upload_text_index

router = APIRouter(prefix="/rag", tags=["rag"])
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]


@router.post("/workspaces/{workspace_id}/search", response_model=RagSearchResponse)
async def search(
    body: RagSearchRequest,
    workspace_id: UUID,
    current_user: AuthenticatedUser,
    session: Session,
    request: Request,
):
    return await search_rag(
        session,
        current_user.user_id,
        workspace_id,
        body,
        getattr(request.app.state, "rag_embedding_provider", None),
    )


@router.get("/workspaces/{workspace_id}/text-indexes", response_model=list[TextIndexStatus])
async def text_index_statuses(
    workspace_id: UUID,
    current_user: AuthenticatedUser,
    session: Session,
):
    return await list_text_index_statuses(session, current_user.user_id, workspace_id)


@router.put("/workspaces/{workspace_id}/text-indexes/{source_type}/{source_id}")
async def put_text_index(
    body: TextIndexUpload,
    workspace_id: UUID,
    source_type: Literal["document", "memo"],
    current_user: AuthenticatedUser,
    session: Session,
    source_id: Annotated[str, Path(min_length=1, max_length=200)],
):
    return await upload_text_index(
        session,
        current_user.user_id,
        workspace_id,
        source_type,
        source_id,
        body,
    )
