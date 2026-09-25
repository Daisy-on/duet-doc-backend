"""Replace legacy 768-dimensional RAG indexes with BGE indexes."""

from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade():
    # Indexes and queued work are disposable; documents and media are not.
    op.execute(
        "TRUNCATE rag_index_jobs, rag_index_runs, rag_cloud_chunks, "
        "rag_cloud_source_indexes, rag_text_chunks, rag_source_indexes"
    )
    op.execute("ALTER TABLE rag_text_chunks ALTER COLUMN embedding TYPE vector(1024)")
    op.execute("ALTER TABLE rag_cloud_chunks ALTER COLUMN embedding TYPE vector(1024)")


def downgrade():
    op.execute(
        "TRUNCATE rag_index_jobs, rag_index_runs, rag_cloud_chunks, "
        "rag_cloud_source_indexes, rag_text_chunks, rag_source_indexes"
    )
    op.execute("ALTER TABLE rag_cloud_chunks ALTER COLUMN embedding TYPE vector(768)")
    op.execute("ALTER TABLE rag_text_chunks ALTER COLUMN embedding TYPE vector(768)")
