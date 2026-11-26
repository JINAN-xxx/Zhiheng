"""Isolate API assistant conversations by application and caller user."""

import sqlalchemy as sa
from alembic import op

revision = '0053_agent_api_conversations'
down_revision = '0052_mcp_gateway'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assistant_conversations', sa.Column('api_application_id', sa.Uuid()))
    op.add_column('assistant_conversations', sa.Column('api_user', sa.String(128)))
    op.create_foreign_key(
        'fk_assistant_conversations_api_application',
        'assistant_conversations',
        'developer_applications',
        ['api_application_id'],
        ['id'],
    )
    op.create_check_constraint(
        'ck_assistant_conversation_api_owner',
        'assistant_conversations',
        '(api_application_id IS NULL AND api_user IS NULL) OR '
        '(api_application_id IS NOT NULL AND api_user IS NOT NULL AND NOT plaza)',
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text('SELECT EXISTS (SELECT 1 FROM assistant_conversations WHERE api_application_id IS NOT NULL)')
    ):
        raise RuntimeError('Back up API conversation data before downgrading.')
    op.drop_constraint('ck_assistant_conversation_api_owner', 'assistant_conversations', type_='check')
    op.drop_constraint('fk_assistant_conversations_api_application', 'assistant_conversations', type_='foreignkey')
    op.drop_column('assistant_conversations', 'api_user')
    op.drop_column('assistant_conversations', 'api_application_id')
