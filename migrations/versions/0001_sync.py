"""Personal workspaces and revision-based sync."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("""
        CREATE TABLE users (
            id uuid PRIMARY KEY, display_name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(), disabled_at timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE workspaces (
            id uuid PRIMARY KEY, owner_user_id uuid NOT NULL REFERENCES users(id),
            name text NOT NULL, sync_sequence bigint NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX workspace_owner ON workspaces(owner_user_id)")
    common = """
        workspace_id uuid NOT NULL REFERENCES workspaces(id), id text NOT NULL,
        revision bigint NOT NULL CHECK (revision > 0),
        created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz, PRIMARY KEY (workspace_id, id)
    """
    op.execute(f"""CREATE TABLE knowledge_bases (
        {common}, name text NOT NULL, description text NOT NULL, icon text NOT NULL
    )""")
    op.execute(f"""CREATE TABLE groups (
        {common}, kb_id text NOT NULL, parent_group_id text,
        name text NOT NULL, sort_order integer NOT NULL, depth integer NOT NULL
        CHECK (depth BETWEEN 0 AND 5),
        FOREIGN KEY (workspace_id, kb_id) REFERENCES knowledge_bases(workspace_id, id),
        UNIQUE (workspace_id, kb_id, id),
        FOREIGN KEY (workspace_id, kb_id, parent_group_id)
            REFERENCES groups(workspace_id, kb_id, id) DEFERRABLE INITIALLY DEFERRED
    )""")
    op.execute(f"""CREATE TABLE documents (
        {common}, kb_id text NOT NULL, group_id text, title text NOT NULL,
        content text NOT NULL, content_format text NOT NULL
            CHECK (content_format IN ('tiptap_json', 'html')),
        FOREIGN KEY (workspace_id, kb_id) REFERENCES knowledge_bases(workspace_id, id),
        FOREIGN KEY (workspace_id, kb_id, group_id)
            REFERENCES groups(workspace_id, kb_id, id) DEFERRABLE INITIALLY DEFERRED
    )""")
    op.execute("""CREATE TABLE sync_changes (
        workspace_id uuid NOT NULL REFERENCES workspaces(id), sequence bigint NOT NULL,
        entity_type text NOT NULL, entity_id text NOT NULL, revision bigint NOT NULL,
        operation text NOT NULL, PRIMARY KEY (workspace_id, sequence)
    )""")
    op.execute("""CREATE TABLE sync_mutations (
        workspace_id uuid NOT NULL REFERENCES workspaces(id), mutation_id uuid NOT NULL,
        request_hash text NOT NULL, result jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (workspace_id, mutation_id)
    )""")
    op.execute("CREATE INDEX document_scope ON documents(workspace_id, kb_id)")
    op.execute("CREATE INDEX group_scope ON groups(workspace_id, kb_id)")


def downgrade():
    for table in (
        "sync_mutations",
        "sync_changes",
        "documents",
        "groups",
        "knowledge_bases",
        "workspaces",
        "users",
    ):
        op.execute(f"DROP TABLE {table}")
