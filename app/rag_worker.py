import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from app.core.config import Settings
from app.core.logging import configure_logging
from app.database import create_database
from app.providers.dashscope_vision import DashScopeVisionProvider
from app.providers.siliconflow_embedding import SiliconFlowEmbeddingProvider
from app.services.cloud_rag_chunker import (
    chunk_document,
    chunk_image_description,
    passage_text,
    text_fingerprint,
)
from app.services.media_storage import MediaStorage
from app.services.rag_text_indexes import source_type_for_kb

logger = logging.getLogger(__name__)
INDEX_VERSION = "bge-v3"
IMAGE_INDEX_VERSION = "bge-v2:qwen3-vl-flash"


async def claim_job(sessions):
    async with sessions() as session, session.begin():
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM rag_index_jobs WHERE status='pending' "
                        "AND available_at<=now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        await session.execute(
            text(
                "UPDATE rag_index_jobs SET status='running',attempts=attempts+1,started_at=now() "
                "WHERE id=:id"
            ),
            {"id": row["id"]},
        )
        await session.execute(
            text(
                "UPDATE rag_index_runs SET status='running',started_at=COALESCE(started_at,now()) "
                "WHERE id=:id"
            ),
            {"id": row["run_id"]},
        )
        return dict(row)


async def load_source(session, job):
    if job["modality"] == "image":
        row = (
            (
                await session.execute(
                    text(
                        "SELECT asset.object_key,asset.md5_hex FROM media_assets asset "
                        "WHERE asset.workspace_id=:wid AND asset.asset_id=:sid "
                        "AND asset.status='ready' AND asset.gc_state='ready' "
                        "AND EXISTS (SELECT 1 FROM document_media_refs ref "
                        "JOIN documents doc ON doc.workspace_id=ref.workspace_id "
                        "AND doc.id=ref.document_id WHERE ref.workspace_id=asset.workspace_id "
                        "AND ref.asset_id=asset.asset_id AND doc.deleted_at IS NULL)"
                    ),
                    {"wid": job["workspace_id"], "sid": job["source_id"]},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row and row["md5_hex"] == job["source_fingerprint"] else None
    row = (
        (
            await session.execute(
                text(
                    "SELECT kb_id,title,content,content_format,revision FROM documents "
                    "WHERE workspace_id=:wid AND id=:sid AND deleted_at IS NULL"
                ),
                {"wid": job["workspace_id"], "sid": job["source_id"]},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["revision"] != job["source_revision"]:
        return None
    if (
        text_fingerprint(row["title"], row["content"], row["content_format"])
        != job["source_fingerprint"]
    ):
        return None
    return dict(row)


async def process_job(sessions, provider, job, vision=None, media_storage=None, media_ttl=900):
    async with sessions() as session:
        source = await load_source(session, job)
        if source is None:
            await finish_job(session, job, "skipped")
            await session.commit()
            return
        chunks = []
        index_version = INDEX_VERSION
        if job["modality"] == "image":
            if vision is None or media_storage is None:
                raise ValueError("Image indexing is not configured")
            image_url = media_storage.sign_read(
                source["object_key"], datetime.now(UTC) + timedelta(seconds=media_ttl)
            )
            description = await vision.describe_image(image_url)
            for chunk in chunk_image_description(description):
                embedding = await provider.embed_text(chunk.content)
                chunks.append(
                    (
                        chunk.index,
                        chunk.content,
                        chunk.content_hash,
                        job["source_id"],
                        embedding,
                        [],
                    )
                )
            index_version = IMAGE_INDEX_VERSION
        else:
            for chunk in chunk_document(
                source["title"],
                source["content"],
                source["content_format"],
                source_type_for_kb(source["kb_id"]),
            ):
                cached = await session.scalar(
                    text(
                        "SELECT embedding::text FROM rag_cloud_chunks WHERE workspace_id=:wid "
                        "AND modality='text' AND content_hash=:hash LIMIT 1"
                    ),
                    {"wid": job["workspace_id"], "hash": chunk.content_hash},
                )
                embedding = (
                    json.loads(cached)
                    if cached
                    else await provider.embed_text(
                        passage_text(source["title"], chunk.heading_path, chunk.content)
                    )
                )
                chunks.append(
                    (
                        chunk.index,
                        chunk.content,
                        chunk.content_hash,
                        None,
                        embedding,
                        chunk.heading_path,
                    )
                )
        if await load_source(session, job) is None:
            await finish_job(session, job, "skipped")
            await session.commit()
            return
        await session.execute(
            text(
                "INSERT INTO rag_cloud_source_indexes "
                "(workspace_id,modality,source_id,source_type,source_revision,source_fingerprint,"
                "embedding_model,embedding_dimension,index_version,status,chunk_count,indexed_at) "
                "VALUES (:wid,:modality,:sid,:source_type,:revision,:fingerprint,:model,:dimension,"
                ":version,'ready',:count,now()) ON CONFLICT (workspace_id,modality,source_id) "
                "DO UPDATE SET source_type=EXCLUDED.source_type,"
                "source_revision=EXCLUDED.source_revision,"
                "source_fingerprint=EXCLUDED.source_fingerprint,embedding_model=EXCLUDED.embedding_model,"
                "embedding_dimension=EXCLUDED.embedding_dimension,index_version=EXCLUDED.index_version,"
                "status='ready',chunk_count=EXCLUDED.chunk_count,error_message=NULL,indexed_at=now(),updated_at=now()"
            ),
            {
                **job,
                "wid": job["workspace_id"],
                "sid": job["source_id"],
                "model": provider.model,
                "dimension": provider.dimension,
                "version": index_version,
                "count": len(chunks),
                "revision": job["source_revision"],
                "fingerprint": job["source_fingerprint"],
            },
        )
        await session.execute(
            text(
                "DELETE FROM rag_cloud_chunks WHERE workspace_id=:wid AND modality=:modality "
                "AND source_id=:sid"
            ),
            {"wid": job["workspace_id"], "modality": job["modality"], "sid": job["source_id"]},
        )
        for index, content, content_hash, asset_id, embedding, heading_path in chunks:
            await session.execute(
                text(
                    "INSERT INTO rag_cloud_chunks "
                    "(workspace_id,modality,source_id,id,chunk_index,content,content_hash,"
                    "asset_id,metadata,embedding) VALUES "
                    "(:wid,:modality,:sid,:id,:idx,:content,:hash,:asset,"
                    "CAST(:metadata AS jsonb),CAST(:embedding AS vector))"
                ),
                {
                    "wid": job["workspace_id"],
                    "modality": job["modality"],
                    "sid": job["source_id"],
                    "id": f"{job['source_id']}:{index}",
                    "idx": index,
                    "content": content,
                    "hash": content_hash,
                    "asset": asset_id,
                    "metadata": json.dumps(
                        {"vision_model": vision.model}
                        if asset_id and vision
                        else {"heading_path": heading_path}
                    ),
                    "embedding": "[" + ",".join(str(value) for value in embedding) + "]",
                },
            )
        await finish_job(session, job, "completed")
        await session.commit()


async def finish_job(session, job, status, error=None):
    await session.execute(
        text(
            "UPDATE rag_index_jobs SET status=:status,error_message=:error,completed_at=now() "
            "WHERE id=:id"
        ),
        {"id": job["id"], "status": status, "error": error},
    )
    counts = (
        (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE status IN ('completed','skipped')) AS done,"
                    "count(*) FILTER (WHERE status='error') AS failed,"
                    "count(*) FILTER (WHERE status IN ('pending','running')) AS active "
                    "FROM rag_index_jobs WHERE run_id=:run"
                ),
                {"run": job["run_id"]},
            )
        )
        .mappings()
        .one()
    )
    run_status = "running"
    if counts["active"] == 0:
        run_status = (
            "partial"
            if counts["failed"] and counts["done"]
            else ("error" if counts["failed"] else "completed")
        )
    await session.execute(
        text(
            "UPDATE rag_index_runs SET status=:status,completed_jobs=:done,failed_jobs=:failed,"
            "completed_at=CASE WHEN :active=0 THEN now() END WHERE id=:run"
        ),
        {"run": job["run_id"], "status": run_status, **counts},
    )


def is_retryable_job_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429} or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


async def fail_job(sessions, settings, job, exc):
    retry = is_retryable_job_error(exc) and job["attempts"] + 1 < settings.rag_worker_max_attempts
    async with sessions() as session:
        if retry:
            await session.execute(
                text(
                    "UPDATE rag_index_jobs SET status='pending',error_message=:error,"
                    "available_at=now()+interval '30 seconds' WHERE id=:id"
                ),
                {"id": job["id"], "error": str(exc)[:1000]},
            )
        else:
            await finish_job(session, job, "error", str(exc)[:1000])
        await session.commit()


async def run() -> None:
    settings = Settings()
    if settings.rag_embedding_dimension != 1024:
        raise ValueError("RAG_EMBEDDING_DIMENSION must be 1024 for the current database schema")
    configure_logging(settings.app_env)
    engine, sessions = create_database(settings)
    async with sessions() as session:
        await session.execute(
            text(
                "UPDATE rag_index_jobs SET status='pending',available_at=now() "
                "WHERE status='running'"
            )
        )
        await session.commit()
    timeout = httpx.Timeout(
        settings.ai_read_timeout_seconds, connect=settings.ai_connect_timeout_seconds
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        provider = SiliconFlowEmbeddingProvider(settings, client)
        try:
            while True:
                job = await claim_job(sessions)
                if job is None:
                    await asyncio.sleep(settings.rag_worker_poll_seconds)
                    continue
                try:
                    vision = (
                        DashScopeVisionProvider(settings, client)
                        if job["modality"] == "image"
                        else None
                    )
                    media = MediaStorage(settings) if vision else None
                    await process_job(
                        sessions, provider, job, vision, media, settings.media_url_ttl_seconds
                    )
                except Exception as exc:
                    logger.exception("Cloud RAG job failed: %s", job["id"])
                    await fail_job(sessions, settings, job, exc)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
