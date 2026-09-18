"""Track model manifest grants for persistent user rate limits."""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.execute("""
        CREATE TABLE model_download_grants (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            model_id text NOT NULL CHECK (length(model_id) BETWEEN 1 AND 128),
            client_ip inet NOT NULL,
            issued_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            CHECK (expires_at > issued_at)
        )
    """)
    op.execute("""
        CREATE INDEX model_download_grant_user_model_time
        ON model_download_grants(user_id, model_id, issued_at DESC)
    """)


def downgrade():
    op.execute("DROP TABLE model_download_grants")
