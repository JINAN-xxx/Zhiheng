"""Add private assistant session mutation versions and deletion tombstones."""

import sqlalchemy as sa
from alembic import op

revision = '0033_assistant_session_actions'
down_revision = '0032_skill_market'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assistant_conversations', sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('assistant_conversations', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM assistant_conversations WHERE deleted_at IS NOT NULL')):
        raise RuntimeError('Preserve assistant session deletion tombstones before downgrading')
    op.drop_column('assistant_conversations', 'deleted_at')
    op.drop_column('assistant_conversations', 'version')
