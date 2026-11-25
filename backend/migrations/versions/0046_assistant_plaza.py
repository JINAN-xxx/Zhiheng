"""Keep published assistants separate from editable drafts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0046_assistant_plaza'
down_revision = '0045_developer_key_controls'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('plaza_config', postgresql.JSONB(none_as_null=True), nullable=True))
    op.add_column('agents', sa.Column('plaza_version', sa.Integer(), nullable=True))
    op.add_column(
        'assistant_conversations', sa.Column('plaza', sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    # Keep the data check and column removal atomic against concurrent writes.
    op.execute('LOCK TABLE agents, assistant_conversations IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM agents WHERE plaza_config IS NOT NULL OR plaza_version IS NOT NULL) '
            'OR EXISTS (SELECT 1 FROM assistant_conversations WHERE plaza)'
        )
    ):
        raise RuntimeError('Assistant plaza data must be preserved. Restore a pre-upgrade backup or fix forward.')
    op.drop_column('assistant_conversations', 'plaza')
    op.drop_column('agents', 'plaza_version')
    op.drop_column('agents', 'plaza_config')
