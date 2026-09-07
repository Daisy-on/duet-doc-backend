"""Password identities and refresh sessions."""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.execute("""
        CREATE TABLE auth_identities (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider text NOT NULL CHECK (length(provider) BETWEEN 1 AND 32),
            provider_subject text NOT NULL CHECK (length(provider_subject) BETWEEN 1 AND 255),
            created_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz,
            UNIQUE (provider, provider_subject)
        )
    """)
    op.execute("CREATE INDEX auth_identity_user ON auth_identities(user_id)")

    op.execute("""
        CREATE TABLE password_credentials (
            user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            password_hash text NOT NULL,
            password_changed_at timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE refresh_sessions (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash char(64) NOT NULL UNIQUE,
            token_family_id uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            replaced_by uuid REFERENCES refresh_sessions(id) DEFERRABLE INITIALLY DEFERRED,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at > created_at)
        )
    """)
    op.execute("""
        CREATE INDEX refresh_session_active_user
        ON refresh_sessions(user_id, expires_at)
        WHERE revoked_at IS NULL
    """)
    op.execute("CREATE INDEX refresh_session_family ON refresh_sessions(token_family_id)")


def downgrade():
    op.execute("DROP TABLE refresh_sessions")
    op.execute("DROP TABLE password_credentials")
    op.execute("DROP TABLE auth_identities")
