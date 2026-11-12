"""Knowledge-owned retrieval settings and durable staging receipts."""

import sqlalchemy as sa
from alembic import op

revision = '0021_knowledge_settings'
down_revision = '0020_knowledge_library'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('knowledge_bases', sa.Column('rerank_model_id', sa.Uuid(), sa.ForeignKey('models.id'), nullable=True))
    op.add_column(
        'knowledge_bases', sa.Column('use_default_rerank', sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.create_table(
        'knowledge_upload_receipts',
        sa.Column('owner_id', sa.Uuid(), primary_key=True),
        sa.Column('request_id', sa.Uuid(), primary_key=True),
        sa.Column('base_id', sa.Uuid(), sa.ForeignKey('knowledge_bases.id'), nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('upload_id', sa.Uuid(), nullable=False, unique=True),
        sa.Column('folder_id', sa.Uuid(), nullable=True),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    # No source, grant or effective index exists to broaden or rebuild in these empty private libraries.
    op.execute(
        sa.text("""
        UPDATE knowledge_bases b SET document_policy='library', version=version+1
        WHERE b.document_policy='legacy' AND b.access='private' AND b.deleted_at IS NULL
          AND b.active_revision IS NULL AND b.active_job_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM knowledge_grants g WHERE g.base_id=b.id)
          AND NOT EXISTS (SELECT 1 FROM knowledge_documents d WHERE d.base_id=b.id)
          AND NOT EXISTS (SELECT 1 FROM knowledge_uploads u WHERE u.base_id=b.id)
    """)
    )


def downgrade() -> None:
    op.drop_table('knowledge_upload_receipts')
    op.drop_column('knowledge_bases', 'use_default_rerank')
    op.drop_column('knowledge_bases', 'rerank_model_id')
