"""Chat sessions and messages in workspace sync."""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    common = """
        workspace_id uuid NOT NULL REFERENCES workspaces(id), id text NOT NULL,
        revision bigint NOT NULL CHECK (revision > 0),
        created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz, PRIMARY KEY (workspace_id, id)
    """
    op.execute(f"""CREATE TABLE chat_sessions (
        {common}, title text NOT NULL, is_pinned boolean NOT NULL DEFAULT false
    )""")
    op.execute(f"""CREATE TABLE chat_messages (
        {common}, session_id text NOT NULL,
        role text NOT NULL CHECK (role IN ('user', 'assistant')),
        content text NOT NULL,
        status text NOT NULL CHECK (status IN ('complete', 'stopped', 'error')),
        web_search_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
        referenced_docs jsonb NOT NULL DEFAULT '[]'::jsonb,
        knowledge_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
        ai_metadata jsonb,
        FOREIGN KEY (workspace_id, session_id)
            REFERENCES chat_sessions(workspace_id, id) DEFERRABLE INITIALLY DEFERRED
    )""")
    op.execute("CREATE INDEX chat_session_scope ON chat_sessions(workspace_id, updated_at DESC)")
    op.execute(
        "CREATE INDEX chat_message_scope ON chat_messages(workspace_id, session_id, created_at, id)"
    )


def downgrade():
    op.execute("DROP TABLE chat_messages")
    op.execute("DROP TABLE chat_sessions")
