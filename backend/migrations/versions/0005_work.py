"""Durable manual Work tasks, frozen members and shared history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0005_work'
down_revision = '0004_agents'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'work_tasks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('title', sa.String(100), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('current_run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id')),
        sa.Column('active_agent_id', sa.Uuid()),
        sa.Column('create_active_agent_id', sa.Uuid(), nullable=False),
        sa.Column('create_client_request_id', sa.Uuid(), nullable=False),
        sa.Column('create_fingerprint', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('owner_id', 'create_client_request_id', name='uq_work_create'),
    )
    op.create_index('ix_work_tasks_owner_id', 'work_tasks', ['owner_id'])
    op.create_table(
        'work_members',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('work_tasks.id'), nullable=False),
        sa.Column('agent_id', sa.Uuid(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('agent_owner_id', sa.Uuid(), nullable=False),
        sa.Column('agent_name_snapshot', sa.String(100), nullable=False),
        sa.Column('published_version', sa.Integer(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('skills', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('removed_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_work_members_task_id', 'work_members', ['task_id'])
    op.create_index(
        'uq_work_active_member',
        'work_members',
        ['task_id', 'agent_id'],
        unique=True,
        postgresql_where=sa.text('removed_at IS NULL'),
    )
    op.create_table(
        'work_messages',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('work_tasks.id'), nullable=False),
        sa.Column('client_request_id', sa.Uuid(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('reasoning', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id')),
        sa.Column('fingerprint', sa.String(64)),
        sa.Column('usage', postgresql.JSONB()),
        sa.Column('error', postgresql.JSONB()),
        sa.Column('agent_id', sa.Uuid()),
        sa.Column('agent_name_snapshot', sa.String(100)),
        sa.Column('published_version', sa.Integer()),
        sa.Column('audit', postgresql.JSONB()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'sequence', name='uq_work_message_sequence'),
        sa.UniqueConstraint('task_id', 'client_request_id', 'role', name='uq_work_request_role'),
    )
    op.create_index('ix_work_messages_task_id', 'work_messages', ['task_id'])
    op.create_index('ix_work_messages_run_id', 'work_messages', ['run_id'])


def downgrade() -> None:
    op.drop_table('work_messages')
    op.drop_table('work_members')
    op.drop_table('work_tasks')
