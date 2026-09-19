"""Track unreferenced media without deleting objects."""

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade():
    op.execute("ALTER TABLE media_assets ADD COLUMN unreferenced_at timestamptz")
    op.execute(
        "ALTER TABLE media_assets ADD COLUMN gc_state text NOT NULL DEFAULT 'ready' "
        "CHECK (gc_state IN ('ready', 'deleting'))"
    )
    op.execute(
        "CREATE INDEX media_gc_candidates ON media_assets(unreferenced_at) "
        "WHERE unreferenced_at IS NOT NULL AND gc_state='ready'"
    )


def downgrade():
    op.execute("DROP INDEX media_gc_candidates")
    op.execute("ALTER TABLE media_assets DROP COLUMN gc_state")
    op.execute("ALTER TABLE media_assets DROP COLUMN unreferenced_at")
