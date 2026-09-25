"""Store client-generated text indexes for cloud retrieval."""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade():
    op.execute("""
        CREATE TABLE rag_source_indexes (
            workspace_id uuid NOT NULL,
            source_id text NOT NULL,
            source_type text NOT NULL CHECK (source_type IN ('document', 'memo')),
            source_revision bigint NOT NULL CHECK (source_revision > 0),
            source_fingerprint text,
            embedding_model text,
            embedding_dimension integer,
            chunker_version text,
            status text NOT NULL CHECK (status IN ('pending', 'ready', 'stale', 'error')),
            chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
            indexed_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, source_id),
            FOREIGN KEY (workspace_id, source_id)
                REFERENCES documents(workspace_id, id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE TABLE rag_text_chunks (
            workspace_id uuid NOT NULL,
            source_id text NOT NULL,
            id text NOT NULL,
            chunk_index integer NOT NULL CHECK (chunk_index >= 0),
            heading_path jsonb NOT NULL DEFAULT '[]'::jsonb,
            content text NOT NULL,
            content_hash text NOT NULL,
            embedding vector(768) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, source_id, id),
            UNIQUE (workspace_id, source_id, chunk_index),
            FOREIGN KEY (workspace_id, source_id)
                REFERENCES rag_source_indexes(workspace_id, source_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX rag_source_status ON rag_source_indexes(workspace_id, status)")
    op.execute("CREATE INDEX rag_chunk_source ON rag_text_chunks(workspace_id, source_id)")


def downgrade():
    op.execute("DROP TABLE rag_text_chunks")
    op.execute("DROP TABLE rag_source_indexes")
