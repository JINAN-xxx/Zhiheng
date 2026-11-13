"""Private assistant conversations freeze publication versions and retain durable turn references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0030_assistant_conversations'
down_revision = '0029_application_governance'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'assistant_conversations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False, index=True),
        sa.Column('agent_id', sa.Uuid(), sa.ForeignKey('agents.id'), nullable=False, index=True),
        sa.Column('create_request_id', sa.Uuid(), nullable=False),
        sa.Column('published_version', sa.Integer(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('skills', postgresql.JSONB(), nullable=False),
        sa.Column('title', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'create_request_id', name='uq_assistant_conversation_request'),
    )
    op.create_table(
        'assistant_turns',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column(
            'conversation_id', sa.Uuid(), sa.ForeignKey('assistant_conversations.id'), nullable=False, index=True
        ),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=False, unique=True),
        sa.Column('sources', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('conversation_id', 'sequence', name='uq_assistant_turn_sequence'),
        sa.UniqueConstraint('owner_id', 'request_id', name='uq_assistant_turn_request'),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM assistant_conversations')):
        raise RuntimeError('Preserve assistant conversation data before downgrading')
    op.drop_table('assistant_turns')
    op.drop_table('assistant_conversations')
