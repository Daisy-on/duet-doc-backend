from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.siliconflow_embedding import SiliconFlowEmbeddingProvider
from app.schemas.rag import RagSearchHit, RagSearchRequest, RagSearchResponse
from app.services.sync_service import workspace_for_user

CURRENT_CLIENT = (
    "idx.status='ready' AND idx.embedding_model='bge-large-zh-v1.5' "
    "AND idx.embedding_dimension=1024 AND idx.chunker_version='v3' "
    "AND idx.source_revision=doc.revision"
)
CURRENT_CLOUD_TEXT = (
    "idx.status='ready' AND idx.embedding_model='BAAI/bge-large-zh-v1.5' "
    "AND idx.embedding_dimension=1024 AND idx.index_version='bge-v3' "
    "AND idx.source_revision=doc.revision"
)
CURRENT_CLOUD_IMAGE = (
    "idx.status='ready' AND idx.embedding_model='BAAI/bge-large-zh-v1.5' "
    "AND idx.embedding_dimension=1024 AND idx.index_version='bge-v2:qwen3-vl-flash' "
    "AND idx.source_fingerprint=asset.md5_hex"
)


def _filters(body: RagSearchRequest) -> dict:
    source_types = set(body.source_types)
    return {
        "document_enabled": "document" in source_types,
        "memo_enabled": "memo" in source_types,
        "image_enabled": "image" in source_types,
        "min_updated": datetime.now(UTC) - timedelta(days=body.time_range_days)
        if body.time_range_days
        else None,
    }


async def search_rag(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    body: RagSearchRequest,
    embedding_provider: SiliconFlowEmbeddingProvider | None,
) -> RagSearchResponse:
    await workspace_for_user(session, workspace_id, user_id)
    params = {"wid": workspace_id, **_filters(body)}
    eligible = await session.scalar(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM rag_source_indexes idx JOIN documents doc "
            "ON doc.workspace_id=idx.workspace_id AND doc.id=idx.source_id "
            f"WHERE idx.workspace_id=:wid AND {CURRENT_CLIENT} "
            "AND doc.deleted_at IS NULL AND doc.updated_at>=COALESCE(:min_updated,doc.updated_at) "
            "AND ((idx.source_type='document' AND :document_enabled) "
            "OR (idx.source_type='memo' AND :memo_enabled)) "
            "UNION ALL SELECT 1 FROM rag_cloud_source_indexes idx JOIN documents doc "
            "ON doc.workspace_id=idx.workspace_id AND doc.id=idx.source_id "
            f"WHERE idx.workspace_id=:wid AND idx.modality='text' AND {CURRENT_CLOUD_TEXT} "
            "AND doc.deleted_at IS NULL AND doc.updated_at>=COALESCE(:min_updated,doc.updated_at) "
            "AND ((idx.source_type='document' AND :document_enabled) "
            "OR (idx.source_type='memo' AND :memo_enabled)) "
            "UNION ALL SELECT 1 FROM rag_cloud_source_indexes idx JOIN media_assets asset "
            "ON asset.workspace_id=idx.workspace_id AND asset.asset_id=idx.source_id "
            f"WHERE idx.workspace_id=:wid AND idx.modality='image' AND {CURRENT_CLOUD_IMAGE} "
            "AND asset.status='ready' AND asset.gc_state='ready' AND :image_enabled "
            "AND EXISTS (SELECT 1 FROM document_media_refs ref JOIN documents doc "
            "ON doc.workspace_id=ref.workspace_id AND doc.id=ref.document_id "
            "WHERE ref.workspace_id=idx.workspace_id AND ref.asset_id=idx.source_id "
            "AND doc.deleted_at IS NULL "
            "AND doc.updated_at>=COALESCE(:min_updated,doc.updated_at))"
            ")"
        ),
        params,
    )
    if not eligible:
        return RagSearchResponse(has_index=False, hits=[])

    embedding = body.embedding
    if embedding is None:
        if not body.allow_cloud_embedding:
            raise HTTPException(409, "Cloud query embedding requires explicit consent")
        if embedding_provider is None:
            raise HTTPException(503, "Cloud query embedding is not configured")
        embedding = await embedding_provider.embed_text(
            f"为这个句子生成表示以用于检索相关文章：{body.query.strip()}"
        )

    params.update(
        {
            "embedding": "[" + ",".join(str(value) for value in embedding) + "]",
            "limit": body.top_k,
            "recent_first": body.sort_by == "updatedAt",
        }
    )
    rows = (
        (
            await session.execute(
                text(
                    "WITH hits AS ("
                    "SELECT idx.source_id,idx.source_type,doc.id AS document_id,doc.kb_id,"
                    "doc.title,chunk.id AS chunk_id,chunk.chunk_index,chunk.heading_path,"
                    "chunk.content,NULL::text AS asset_id,doc.updated_at AS source_updated_at,"
                    "chunk.embedding <=> CAST(:embedding AS vector) AS distance "
                    "FROM rag_text_chunks chunk JOIN rag_source_indexes idx "
                    "ON idx.workspace_id=chunk.workspace_id AND idx.source_id=chunk.source_id "
                    "JOIN documents doc ON doc.workspace_id=idx.workspace_id "
                    "AND doc.id=idx.source_id "
                    f"WHERE idx.workspace_id=:wid AND {CURRENT_CLIENT} "
                    "AND doc.deleted_at IS NULL "
                    "AND doc.updated_at>=COALESCE(:min_updated,doc.updated_at) "
                    "AND ((idx.source_type='document' AND :document_enabled) "
                    "OR (idx.source_type='memo' AND :memo_enabled)) "
                    "UNION ALL "
                    "SELECT idx.source_id,idx.source_type,doc.id,doc.kb_id,doc.title,"
                    "chunk.id,chunk.chunk_index,"
                    "COALESCE(chunk.metadata->'heading_path','[]'::jsonb),"
                    "chunk.content,NULL::text,"
                    "doc.updated_at,chunk.embedding <=> CAST(:embedding AS vector) "
                    "FROM rag_cloud_chunks chunk JOIN rag_cloud_source_indexes idx "
                    "ON idx.workspace_id=chunk.workspace_id AND idx.modality=chunk.modality "
                    "AND idx.source_id=chunk.source_id JOIN documents doc "
                    "ON doc.workspace_id=idx.workspace_id AND doc.id=idx.source_id "
                    f"WHERE idx.workspace_id=:wid AND idx.modality='text' AND {CURRENT_CLOUD_TEXT} "
                    "AND doc.deleted_at IS NULL "
                    "AND doc.updated_at>=COALESCE(:min_updated,doc.updated_at) "
                    "AND ((idx.source_type='document' AND :document_enabled) "
                    "OR (idx.source_type='memo' AND :memo_enabled)) "
                    "AND NOT EXISTS (SELECT 1 FROM rag_source_indexes client "
                    "WHERE client.workspace_id=idx.workspace_id AND client.source_id=idx.source_id "
                    "AND client.status='ready' AND client.source_revision=doc.revision "
                    "AND client.embedding_model='bge-large-zh-v1.5' "
                    "AND client.embedding_dimension=1024 AND client.chunker_version='v3') "
                    "UNION ALL "
                    "SELECT idx.source_id,'image'::text,doc.id,doc.kb_id,doc.title,"
                    "chunk.id,chunk.chunk_index,'[]'::jsonb,chunk.content,chunk.asset_id,"
                    "doc.updated_at,chunk.embedding <=> CAST(:embedding AS vector) "
                    "FROM rag_cloud_chunks chunk JOIN rag_cloud_source_indexes idx "
                    "ON idx.workspace_id=chunk.workspace_id AND idx.modality=chunk.modality "
                    "AND idx.source_id=chunk.source_id JOIN media_assets asset "
                    "ON asset.workspace_id=idx.workspace_id AND asset.asset_id=idx.source_id "
                    "JOIN LATERAL (SELECT doc.id,doc.kb_id,doc.title,doc.updated_at "
                    "FROM document_media_refs ref JOIN documents doc "
                    "ON doc.workspace_id=ref.workspace_id AND doc.id=ref.document_id "
                    "WHERE ref.workspace_id=idx.workspace_id AND ref.asset_id=idx.source_id "
                    "AND doc.deleted_at IS NULL "
                    "AND doc.updated_at>=COALESCE(:min_updated,doc.updated_at) "
                    "ORDER BY doc.id LIMIT 1) doc ON true "
                    f"WHERE idx.workspace_id=:wid AND idx.modality='image' "
                    f"AND {CURRENT_CLOUD_IMAGE} "
                    "AND asset.status='ready' AND asset.gc_state='ready' AND :image_enabled"
                    "), ranked AS (SELECT hits.*,row_number() OVER ("
                    "PARTITION BY source_id ORDER BY distance ASC,chunk_index) AS source_rank "
                    "FROM hits) SELECT * FROM ranked WHERE source_rank<=2 ORDER BY "
                    "CASE WHEN :recent_first THEN source_updated_at END DESC NULLS LAST,"
                    "distance ASC,source_id,chunk_index LIMIT :limit"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return RagSearchResponse(
        has_index=True,
        hits=[
            RagSearchHit(
                **{
                    key: row[key]
                    for key in (
                        "source_id",
                        "source_type",
                        "document_id",
                        "kb_id",
                        "title",
                        "chunk_id",
                        "chunk_index",
                        "heading_path",
                        "content",
                        "asset_id",
                    )
                },
                score=max(0.0, 1.0 - row["distance"]),
                source_updated_at=row["source_updated_at"],
            )
            for row in rows
        ],
    )
