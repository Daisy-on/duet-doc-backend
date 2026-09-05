"""Preserve atomic change groups and immutable pull snapshots."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    # This foundation migration precedes the first client rollout.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM sync_changes) THEN
            RAISE EXCEPTION 'Snapshot migration requires an unused sync change log';
        END IF;
    END $$""")
    op.execute("ALTER TABLE sync_changes ADD COLUMN group_end bigint NOT NULL")
    op.execute("ALTER TABLE sync_changes ADD COLUMN snapshot jsonb NOT NULL")


def downgrade():
    op.execute("ALTER TABLE sync_changes DROP COLUMN snapshot, DROP COLUMN group_end")
