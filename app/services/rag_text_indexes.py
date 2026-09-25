import json
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.rag import TextIndexUpload
from app.services.sync_service import workspace_for_user

MEMO_KB_ID = "kb-memo-system"


def source_type_for_kb(kb_id: str) -> Literal["document", "memo"]:
    return "memo" if kb_id == MEMO_KB_ID else "document"


async def upload_text_index(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    source_type: Literal["document", "memo"],
    source_id: str,
    body: TextIndexUpload,
):
    await workspace_for_user(session, workspace_id, user_id)
    document = (
        (
            await session.execute(
                text(
                    "SELECT revision,kb_id FROM documents "
                    "WHERE workspace_id=:wid AND id=:sid AND deleted_at IS NULL FOR UPDATE"
                ),
                {"wid": workspace_id, "sid": source_id},
            )
        )
        .mappings()
        .first()
    )
    if document is None:
        raise HTTPException(404, "Source not found")
    if source_type_for_kb(document["kb_id"]) != source_type:
        raise HTTPException(409, {"code": "SOURCE_TYPE_MISMATCH"})
    if document["revision"] != body.source_revision:
        raise HTTPException(
            409,
            {
                "code": "SOURCE_REVISION_MISMATCH",
                "current_revision": document["revision"],
            },
        )

    params = {
        "wid": workspace_id,
        "sid": source_id,
        "source_type": source_type,
        "revision": body.source_revision,
        "fingerprint": body.source_fingerprint,
        "model": body.embedding_model,
        "dimension": body.embedding_dimension,
        "chunker": body.chunker_version,
        "count": len(body.chunks),
    }
    await session.execute(
        text(
            "INSERT INTO rag_source_indexes "
            "(workspace_id,source_id,source_type,source_revision,source_fingerprint,"
            "embedding_model,embedding_dimension,chunker_version,status,chunk_count,indexed_at) "
            "VALUES (:wid,:sid,:source_type,:revision,:fingerprint,:model,:dimension,:chunker,"
            "'ready',:count,now()) ON CONFLICT (workspace_id,source_id) DO UPDATE SET "
            "source_type=EXCLUDED.source_type,source_revision=EXCLUDED.source_revision,"
            "source_fingerprint=EXCLUDED.source_fingerprint,"
            "embedding_model=EXCLUDED.embedding_model,"
            "embedding_dimension=EXCLUDED.embedding_dimension,"
            "chunker_version=EXCLUDED.chunker_version,status='ready',"
            "chunk_count=EXCLUDED.chunk_count,indexed_at=now(),updated_at=now()"
        ),
        params,
    )
    await session.execute(
        text("DELETE FROM rag_text_chunks WHERE workspace_id=:wid AND source_id=:sid"), params
    )
    for chunk in body.chunks:
        await session.execute(
            text(
                "INSERT INTO rag_text_chunks "
                "(workspace_id,source_id,id,chunk_index,heading_path,content,content_hash,"
                "embedding) "
                "VALUES (:wid,:sid,:id,:chunk_index,CAST(:heading_path AS jsonb),:content,"
                ":content_hash,CAST(:embedding AS vector))"
            ),
            {
                **params,
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "heading_path": json.dumps(chunk.heading_path),
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "embedding": "[" + ",".join(str(value) for value in chunk.embedding) + "]",
            },
        )
    return {"source_id": source_id, "status": "ready", "chunk_count": len(body.chunks)}


async def list_text_index_statuses(
    session: AsyncSession, user_id: UUID, workspace_id: UUID
):
    await workspace_for_user(session, workspace_id, user_id)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT * FROM rag_source_indexes WHERE workspace_id=:wid "
                    "ORDER BY updated_at DESC,source_id"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]
