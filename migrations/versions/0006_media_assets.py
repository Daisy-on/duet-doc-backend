"""Workspace media assets and document references."""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade():
    op.execute("""
        CREATE TABLE media_assets (
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            asset_id uuid NOT NULL,
            object_key text NOT NULL UNIQUE,
            content_type text NOT NULL,
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            md5_hex text NOT NULL CHECK (md5_hex ~ '^[0-9a-f]{32}$'),
            status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready')),
            created_at timestamptz NOT NULL DEFAULT now(),
            ready_at timestamptz,
            PRIMARY KEY (workspace_id, asset_id),
            CHECK ((status = 'ready') = (ready_at IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE document_media_refs (
            workspace_id uuid NOT NULL,
            document_id text NOT NULL,
            asset_id uuid NOT NULL,
            PRIMARY KEY (workspace_id, document_id, asset_id),
            FOREIGN KEY (workspace_id, document_id) REFERENCES documents(workspace_id, id),
            FOREIGN KEY (workspace_id, asset_id) REFERENCES media_assets(workspace_id, asset_id)
        )
    """)
    op.execute("CREATE INDEX media_ref_asset ON document_media_refs(workspace_id, asset_id)")


def downgrade():
    op.execute("DROP TABLE document_media_refs")
    op.execute("DROP TABLE media_assets")
