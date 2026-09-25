"""Cloud multimodal indexes and explicit indexing jobs."""

from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade():
    op.execute("""
        CREATE TABLE rag_cloud_source_indexes (
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            modality text NOT NULL CHECK (modality IN ('text', 'image')),
            source_id text NOT NULL,
            source_type text NOT NULL CHECK (source_type IN ('document', 'memo', 'image')),
            source_revision bigint,
            source_fingerprint text NOT NULL,
            embedding_model text NOT NULL,
            embedding_dimension integer NOT NULL,
            index_version text NOT NULL,
            status text NOT NULL CHECK (status IN ('ready', 'stale', 'error')),
            chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
            error_message text,
            indexed_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, modality, source_id)
        )
    """)
    op.execute("""
        CREATE TABLE rag_cloud_chunks (
            workspace_id uuid NOT NULL,
            modality text NOT NULL,
            source_id text NOT NULL,
            id text NOT NULL,
            chunk_index integer NOT NULL CHECK (chunk_index >= 0),
            content text,
            content_hash text NOT NULL,
            asset_id text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            embedding vector(768) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, modality, source_id, id),
            UNIQUE (workspace_id, modality, source_id, chunk_index),
            FOREIGN KEY (workspace_id, modality, source_id)
                REFERENCES rag_cloud_source_indexes(workspace_id, modality, source_id)
                ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE TABLE rag_index_runs (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            requested_by_user_id uuid NOT NULL REFERENCES users(id),
            status text NOT NULL CHECK (
                status IN ('pending', 'running', 'completed', 'partial', 'error')
            ),
            total_jobs integer NOT NULL DEFAULT 0,
            completed_jobs integer NOT NULL DEFAULT 0,
            failed_jobs integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            completed_at timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE rag_index_jobs (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES rag_index_runs(id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            modality text NOT NULL CHECK (modality IN ('text', 'image')),
            source_type text NOT NULL CHECK (source_type IN ('document', 'memo', 'image')),
            source_id text NOT NULL,
            source_revision bigint,
            source_fingerprint text NOT NULL,
            status text NOT NULL CHECK (
                status IN ('pending', 'running', 'completed', 'skipped', 'error')
            ),
            attempts integer NOT NULL DEFAULT 0,
            error_message text,
            available_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            completed_at timestamptz,
            UNIQUE (run_id, modality, source_id)
        )
    """)
    op.execute("CREATE INDEX rag_cloud_status ON rag_cloud_source_indexes(workspace_id,status)")
    op.execute(
        "CREATE INDEX rag_cloud_chunk_hash ON rag_cloud_chunks(workspace_id,modality,content_hash)"
    )
    op.execute("CREATE INDEX rag_job_queue ON rag_index_jobs(status,available_at,created_at)")
    op.execute("CREATE INDEX rag_run_workspace ON rag_index_runs(workspace_id,created_at DESC)")


def downgrade():
    op.execute("DROP TABLE rag_index_jobs")
    op.execute("DROP TABLE rag_index_runs")
    op.execute("DROP TABLE rag_cloud_chunks")
    op.execute("DROP TABLE rag_cloud_source_indexes")
