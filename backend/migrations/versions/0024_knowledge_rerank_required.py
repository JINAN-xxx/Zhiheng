"""Retire per-library rerank disabling without touching indexed materials."""

import sqlalchemy as sa
from alembic import op

revision = '0024_knowledge_rerank_required'
down_revision = '0023_knowledge_link_only'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            'UPDATE knowledge_bases SET use_default_rerank=true, version=version+1, updated_at=CURRENT_TIMESTAMP '
            'WHERE NOT use_default_rerank AND rerank_model_id IS NULL'
        )
    )
    op.create_check_constraint(
        'ck_knowledge_bases_rerank_required',
        'knowledge_bases',
        'use_default_rerank OR rerank_model_id IS NOT NULL',
    )


def downgrade() -> None:
    op.drop_constraint('ck_knowledge_bases_rerank_required', 'knowledge_bases', type_='check')
