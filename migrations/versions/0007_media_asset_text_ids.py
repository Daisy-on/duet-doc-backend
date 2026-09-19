"""Allow existing client asset identifiers in cloud media records."""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    op.execute("""
        ALTER TABLE document_media_refs
        DROP CONSTRAINT document_media_refs_workspace_id_asset_id_fkey
    """)
    op.execute("""
        ALTER TABLE document_media_refs
        ALTER COLUMN asset_id TYPE text USING asset_id::text
    """)
    op.execute("""
        ALTER TABLE media_assets
        ALTER COLUMN asset_id TYPE text USING asset_id::text
    """)
    op.execute("""
        ALTER TABLE media_assets
        ADD CONSTRAINT media_asset_id_format
        CHECK (length(asset_id) BETWEEN 1 AND 128 AND asset_id ~ '^[A-Za-z0-9_-]+$')
    """)
    op.execute("""
        ALTER TABLE document_media_refs
        ADD CONSTRAINT document_media_refs_workspace_id_asset_id_fkey
        FOREIGN KEY (workspace_id, asset_id)
        REFERENCES media_assets(workspace_id, asset_id)
    """)


def downgrade():
    op.execute("""
        ALTER TABLE document_media_refs
        DROP CONSTRAINT document_media_refs_workspace_id_asset_id_fkey
    """)
    op.execute("ALTER TABLE media_assets DROP CONSTRAINT media_asset_id_format")
    op.execute("""
        ALTER TABLE media_assets
        ALTER COLUMN asset_id TYPE uuid USING asset_id::uuid
    """)
    op.execute("""
        ALTER TABLE document_media_refs
        ALTER COLUMN asset_id TYPE uuid USING asset_id::uuid
    """)
    op.execute("""
        ALTER TABLE document_media_refs
        ADD CONSTRAINT document_media_refs_workspace_id_asset_id_fkey
        FOREIGN KEY (workspace_id, asset_id)
        REFERENCES media_assets(workspace_id, asset_id)
    """)
