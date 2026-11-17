"""Retire manual rolling-summary state and bind summaries to parent attempts."""

import sqlalchemy as sa
from alembic import op

revision = '0036_chat_runtime_cleanup'
down_revision = '0035_chat_checkpoints'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # uuid5 child identities cannot be reversed into a parent identity. Preserve
    # terminal history as unknown instead of inventing provenance; pending legacy
    # work must first finish or be explicitly cancelled under the old runtime.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM chat_summary_tasks WHERE status='pending')")):
        raise RuntimeError('Pending chat summaries must finish or be cancelled before runtime cleanup')
    op.add_column(
        'chat_context_states', sa.Column('checkpoint_cleanup_after', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('chat_summary_tasks', sa.Column('parent_run_id', sa.Uuid(), nullable=True))
    op.add_column('chat_summary_tasks', sa.Column('parent_attempt_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_chat_summary_parent_run', 'chat_summary_tasks', 'runtime_runs', ['parent_run_id'], ['id'])
    op.create_index('ix_chat_summary_tasks_parent_run_id', 'chat_summary_tasks', ['parent_run_id'])
    op.create_check_constraint(
        'ck_chat_summary_parent_identity',
        'chat_summary_tasks',
        '(parent_run_id IS NULL) = (parent_attempt_id IS NULL)',
    )
    op.create_check_constraint(
        'ck_chat_summary_pending_parent', 'chat_summary_tasks', "status <> 'pending' OR parent_run_id IS NOT NULL"
    )
    op.create_index(
        'ix_chat_context_snapshots_checkpoint',
        'chat_context_snapshots',
        ['conversation_id', 'agent_checkpoint_id'],
    )
    op.drop_constraint('uq_chat_summary_retry_request', 'chat_summary_tasks', type_='unique')
    op.drop_column('chat_summary_tasks', 'previous_task_id')
    op.drop_column('chat_summary_tasks', 'retry_request_id')
    op.drop_column('chat_context_snapshots', 'summary_task_id')
    op.drop_index('ix_chat_context_states_dirty', table_name='chat_context_states')
    op.drop_column('chat_context_states', 'target_covered_count')
    op.drop_column('chat_context_states', 'dirty')
    op.drop_column('chat_context_states', 'summary_task_id')
    op.drop_column('chat_context_states', 'last_run_id')
    op.execute(
        sa.text(
            "UPDATE chat_messages SET request_context=request_context-'knowledge_base_ids' "
            "WHERE request_context ? 'knowledge_base_ids'"
        )
    )
    op.drop_column('chat_messages', 'knowledge_sources')


def downgrade() -> None:
    raise RuntimeError('Manual summary state cannot be reconstructed after runtime cleanup; restore a database backup')
