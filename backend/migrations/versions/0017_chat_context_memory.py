"""Add durable rolling conversation memory without rewriting historical requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0017_chat_context_memory'
down_revision = '0016_file_purpose'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('models', sa.Column('context_window_tokens', sa.Integer(), nullable=True))
    op.create_table(
        'chat_context_states',
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('chat_conversations.id'), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('target_covered_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dirty', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('summary_task_id', sa.Uuid(), nullable=True),
        sa.Column('pending_task_id', sa.Uuid(), nullable=True),
        sa.Column('last_run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
    )
    op.create_index('ix_chat_context_states_dirty', 'chat_context_states', ['dirty'])
    op.create_table(
        'chat_summary_tasks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('chat_conversations.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('expected_version', sa.Integer(), nullable=False),
        sa.Column('covered_count', sa.Integer(), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('source_refs', postgresql.JSONB(), nullable=False),
        sa.Column('file_refs', postgresql.JSONB(), nullable=False),
        sa.Column('previous_task_id', sa.Uuid(), nullable=True),
        sa.Column('retry_request_id', sa.Uuid(), nullable=True),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=True, unique=True),
        sa.Column('model_binding', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'retry_request_id', name='uq_chat_summary_retry_request'),
    )
    op.create_index('ix_chat_summary_tasks_conversation_id', 'chat_summary_tasks', ['conversation_id'])
    op.create_table(
        'chat_context_snapshots',
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('chat_conversations.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('summary_task_id', sa.Uuid(), nullable=True),
        sa.Column('summary_version', sa.Integer(), nullable=False),
        sa.Column('source_refs', postgresql.JSONB(), nullable=False),
        sa.Column('file_refs', postgresql.JSONB(), nullable=False),
        sa.Column('estimated_tokens', sa.Integer(), nullable=False),
        sa.Column('input_budget', sa.Integer(), nullable=False),
        sa.Column('dropped_turns', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_chat_context_snapshots_conversation_id', 'chat_context_snapshots', ['conversation_id'])


def downgrade() -> None:
    op.drop_table('chat_context_snapshots')
    op.drop_table('chat_summary_tasks')
    op.drop_table('chat_context_states')
    op.drop_column('models', 'context_window_tokens')
