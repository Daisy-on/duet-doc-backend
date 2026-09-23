from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.cloud_rag import (
    CloudRagCoverage,
    CloudRagPlan,
    CloudRagRunCreated,
    CloudRagRunStatus,
)
from app.services.cloud_rag_chunker import chunk_document, text_fingerprint
from app.services.rag_text_indexes import source_type_for_kb
from app.services.sync_service import workspace_for_user

EMBEDDING_MODEL = "BAAI/bge-large-zh-v1.5"
EMBEDDING_DIMENSION = 1024
INDEX_VERSION = "bge-v1"


async def _current_sources(session: AsyncSession, workspace_id: UUID):
    return (
        (
            await session.execute(
                text(
                    "SELECT id,kb_id,title,content,content_format,revision FROM documents "
                    "WHERE workspace_id=:wid AND deleted_at IS NULL ORDER BY id"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .all()
    )


async def _client_indexed_revisions(session: AsyncSession, workspace_id: UUID):
    rows = (
        (
            await session.execute(
                text(
                    "SELECT source_id,source_revision FROM rag_source_indexes "
                    "WHERE workspace_id=:wid AND status='ready' "
                    "AND embedding_model='bge-large-zh-v1.5' AND embedding_dimension=1024 "
                    "AND chunker_version='v2'"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .all()
    )
    return {row["source_id"]: row["source_revision"] for row in rows}


async def cloud_rag_coverage(
    session: AsyncSession, user_id: UUID, workspace_id: UUID
) -> CloudRagCoverage:
    await workspace_for_user(session, workspace_id, user_id)
    documents = await _current_sources(session, workspace_id)
    fingerprints = {
        ("text", row["id"]): text_fingerprint(row["title"], row["content"], row["content_format"])
        for row in documents
    }
    rows = (
        (
            await session.execute(
                text(
                    "SELECT modality,source_id,source_fingerprint,status,embedding_model,"
                    "embedding_dimension,index_version "
                    "FROM rag_cloud_source_indexes WHERE workspace_id=:wid"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .all()
    )
    indexes = {(row["modality"], row["source_id"]): row for row in rows}
    ready = sum(
        1
        for key, fingerprint in fingerprints.items()
        if (row := indexes.get(key))
        and row["status"] == "ready"
        and row["source_fingerprint"] == fingerprint
        and row["embedding_model"] == EMBEDDING_MODEL
        and row["embedding_dimension"] == EMBEDDING_DIMENSION
        and row["index_version"] == INDEX_VERSION
    )
    stale = sum(1 for key in fingerprints if key in indexes) - ready
    missing = len(fingerprints) - ready - stale
    has_client_index = bool(
        await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM rag_source_indexes idx JOIN documents doc "
                "ON doc.workspace_id=idx.workspace_id AND doc.id=idx.source_id "
                "WHERE idx.workspace_id=:wid AND idx.status='ready' "
                "AND idx.embedding_model='bge-large-zh-v1.5' "
                "AND idx.embedding_dimension=1024 "
                "AND idx.source_revision=doc.revision AND doc.deleted_at IS NULL)"
            ),
            {"wid": workspace_id},
        )
    )
    active_run = (
        (
            await session.execute(
                text(
                    "SELECT id,status FROM rag_index_runs WHERE workspace_id=:wid "
                    "AND status IN ('pending','running') ORDER BY created_at DESC LIMIT 1"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .first()
    )
    return CloudRagCoverage(
        current_sources=len(fingerprints),
        ready_sources=ready,
        stale_sources=stale,
        missing_sources=missing,
        has_client_index=has_client_index,
        has_cloud_index=ready > 0,
        has_any_index=has_client_index or ready > 0,
        active_run_id=active_run["id"] if active_run else None,
        active_run_status=active_run["status"] if active_run else None,
    )


async def cloud_rag_plan(session: AsyncSession, user_id: UUID, workspace_id: UUID) -> CloudRagPlan:
    await workspace_for_user(session, workspace_id, user_id)
    documents = await _current_sources(session, workspace_id)
    existing_rows = (
        (
            await session.execute(
                text(
                    "SELECT modality,source_id,source_fingerprint,status,embedding_model,"
                    "embedding_dimension,index_version "
                    "FROM rag_cloud_source_indexes WHERE workspace_id=:wid"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .all()
    )
    existing = {(row["modality"], row["source_id"]): row for row in existing_rows}
    client_revisions = await _client_indexed_revisions(session, workspace_id)
    selected_documents = []
    chunk_count = character_count = 0
    for row in documents:
        if client_revisions.get(row["id"]) == row["revision"]:
            continue
        fingerprint = text_fingerprint(row["title"], row["content"], row["content_format"])
        current = existing.get(("text", row["id"]))
        if (
            current
            and current["status"] == "ready"
            and current["source_fingerprint"] == fingerprint
            and current["embedding_model"] == EMBEDDING_MODEL
            and current["embedding_dimension"] == EMBEDDING_DIMENSION
            and current["index_version"] == INDEX_VERSION
        ):
            continue
        chunks = chunk_document(row["title"], row["content"], row["content_format"])
        if not chunks:
            continue
        selected_documents.append(row)
        chunk_count += len(chunks)
        character_count += sum(len(chunk.content) for chunk in chunks)
    return CloudRagPlan(
        document_count=len(selected_documents),
        text_chunk_count=chunk_count,
        text_character_count=character_count,
        image_count=0,
        total_jobs=len(selected_documents),
    )


async def create_cloud_rag_run(
    session: AsyncSession, user_id: UUID, workspace_id: UUID
) -> CloudRagRunCreated:
    await workspace_for_user(session, workspace_id, user_id, lock=True)
    active = (
        (
            await session.execute(
                text(
                    "SELECT id,status,total_jobs FROM rag_index_runs "
                    "WHERE workspace_id=:wid AND status IN ('pending','running') "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .first()
    )
    if active is not None:
        return CloudRagRunCreated(
            run_id=active["id"], status=active["status"], total_jobs=active["total_jobs"]
        )
    documents = await _current_sources(session, workspace_id)
    plan = await cloud_rag_plan(session, user_id, workspace_id)
    run_id = uuid4()
    status = "pending" if plan.total_jobs else "completed"
    await session.execute(
        text(
            "INSERT INTO rag_index_runs "
            "(id,workspace_id,requested_by_user_id,status,total_jobs,completed_at) "
            "VALUES (:id,:wid,:uid,:status,:total,CASE WHEN :total=0 THEN now() END)"
        ),
        {
            "id": run_id,
            "wid": workspace_id,
            "uid": user_id,
            "status": status,
            "total": plan.total_jobs,
        },
    )
    existing_rows = (
        (
            await session.execute(
                text(
                    "SELECT modality,source_id,source_fingerprint,status,embedding_model,"
                    "embedding_dimension,index_version "
                    "FROM rag_cloud_source_indexes WHERE workspace_id=:wid"
                ),
                {"wid": workspace_id},
            )
        )
        .mappings()
        .all()
    )
    existing = {(row["modality"], row["source_id"]): row for row in existing_rows}
    client_revisions = await _client_indexed_revisions(session, workspace_id)
    jobs = []
    for row in documents:
        if client_revisions.get(row["id"]) == row["revision"]:
            continue
        fingerprint = text_fingerprint(row["title"], row["content"], row["content_format"])
        current = existing.get(("text", row["id"]))
        if (
            current
            and current["status"] == "ready"
            and current["source_fingerprint"] == fingerprint
            and current["embedding_model"] == EMBEDDING_MODEL
            and current["embedding_dimension"] == EMBEDDING_DIMENSION
            and current["index_version"] == INDEX_VERSION
        ):
            continue
        if not chunk_document(row["title"], row["content"], row["content_format"]):
            continue
        jobs.append(
            ("text", source_type_for_kb(row["kb_id"]), row["id"], row["revision"], fingerprint)
        )
    for modality, source_type, source_id, revision, fingerprint in jobs:
        await session.execute(
            text(
                "INSERT INTO rag_index_jobs "
                "(id,run_id,workspace_id,modality,source_type,source_id,source_revision,"
                "source_fingerprint,status) VALUES "
                "(:id,:run,:wid,:modality,:source_type,:source_id,:revision,:fingerprint,'pending')"
            ),
            {
                "id": uuid4(),
                "run": run_id,
                "wid": workspace_id,
                "modality": modality,
                "source_type": source_type,
                "source_id": source_id,
                "revision": revision,
                "fingerprint": fingerprint,
            },
        )
    await session.commit()
    return CloudRagRunCreated(run_id=run_id, status=status, total_jobs=len(jobs))


async def cloud_rag_run_status(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, run_id: UUID
) -> CloudRagRunStatus:
    await workspace_for_user(session, workspace_id, user_id)
    row = (
        (
            await session.execute(
                text(
                    "SELECT id,status,total_jobs,completed_jobs,failed_jobs,"
                    "created_at,completed_at "
                    "FROM rag_index_runs WHERE id=:id AND workspace_id=:wid"
                ),
                {"id": run_id, "wid": workspace_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(404, "Index run not found")
    return CloudRagRunStatus(
        run_id=row["id"], **{key: value for key, value in row.items() if key != "id"}
    )
