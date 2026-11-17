"""Goal-driven work tasks, managed file versions and fenced sandbox execution facts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0041_work_agent'
down_revision = '0040_assistant_agents'
branch_labels = None
depends_on = None

CHECKPOINT_MIGRATIONS = (
    'CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)',
    "CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint JSONB NOT NULL, metadata JSONB NOT NULL DEFAULT '{}', PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))",
    "CREATE TABLE IF NOT EXISTS checkpoint_blobs (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL, version TEXT NOT NULL, type TEXT NOT NULL, blob BYTEA, PRIMARY KEY (thread_id, checkpoint_ns, channel, version))",
    "CREATE TABLE IF NOT EXISTS checkpoint_writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, blob BYTEA NOT NULL, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))",
    'ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP NOT NULL',
    'SELECT 1',
    'CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id)',
    'CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)',
    'CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)',
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''",
)


def upgrade() -> None:
    active = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM runtime_runs WHERE resource_type='work_turn' AND status IN ('pending','running','cancel_requested')"
        )
    )
    preparing = op.get_bind().scalar(
        sa.text("""SELECT count(*) FROM knowledge_retrieval_intents
        WHERE resource_type='work_turn'
          AND status NOT IN ('admitted','completed','failed','cancelled','interrupted')""")
    )
    if active or preparing:
        raise RuntimeError('Finish or cancel existing workspace runs and knowledge preparations before upgrading')
    op.add_column('work_tasks', sa.Column('mode', sa.String(16), nullable=False, server_default='legacy'))
    op.add_column('work_tasks', sa.Column('configuration', postgresql.JSONB(), nullable=False, server_default='{}'))
    op.add_column('work_tasks', sa.Column('execution_state', sa.String(24), nullable=False, server_default='idle'))
    op.add_column('work_tasks', sa.Column('plan', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.add_column('work_tasks', sa.Column('agent_checkpoint_id', sa.String(128), nullable=True))
    op.add_column('work_tasks', sa.Column('workspace_files', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.add_column('work_tasks', sa.Column('checkpoint_cleanup_after', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        'ix_work_checkpoint_cleanup',
        'work_tasks',
        ['checkpoint_cleanup_after', 'id'],
        postgresql_where=sa.text('deleted_at IS NOT NULL'),
    )
    op.alter_column('work_tasks', 'create_active_agent_id', existing_type=sa.Uuid(), nullable=True)
    op.create_table(
        'work_inputs',
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('work_tasks.id'), primary_key=True),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('mime', sa.String(100), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('file_version', sa.Integer(), nullable=False),
        sa.Column('acl_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'name', 'version', name='uq_work_input_version'),
    )
    op.create_table(
        'work_steps',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('work_tasks.id'), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=False),
        sa.Column('attempt_id', sa.Uuid(), nullable=False),
        sa.Column('action_id', sa.String(200), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('details', postgresql.JSONB(), nullable=False),
        sa.Column('result', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'action_id', name='uq_work_step_action'),
    )
    op.create_index('ix_work_steps_task_id', 'work_steps', ['task_id'])
    op.create_index('ix_work_steps_run_id', 'work_steps', ['run_id'])
    op.create_table(
        'work_artifacts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('work_tasks.id'), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('preview_file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=True),
        sa.Column('logical_path', sa.String(500), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('mime', sa.String(100), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('validation', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'logical_path', 'version', name='uq_work_artifact_version'),
    )
    op.create_index('ix_work_artifacts_task_id', 'work_artifacts', ['task_id'])
    op.create_index('ix_work_artifacts_run_id', 'work_artifacts', ['run_id'])
    op.create_table(
        'work_sandbox_leases',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('work_tasks.id'), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=False),
        sa.Column('attempt_id', sa.Uuid(), nullable=False),
        sa.Column('sandbox_id', sa.String(200), nullable=True),
        sa.Column('image', sa.String(500), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('error', sa.String(500), nullable=True),
    )
    for column in ('task_id', 'run_id', 'expires_at'):
        op.create_index('ix_work_sandbox_leases_' + column, 'work_sandbox_leases', [column])
    op.execute('CREATE SCHEMA work_checkpoints')
    op.execute('REVOKE ALL ON SCHEMA work_checkpoints FROM PUBLIC')
    op.execute('SET LOCAL search_path TO work_checkpoints,pg_catalog')
    for version, statement in enumerate(CHECKPOINT_MIGRATIONS):
        op.execute(sa.text(statement))
        op.execute(sa.text('INSERT INTO checkpoint_migrations (v) VALUES (:version)').bindparams(version=version))
    op.execute('SET LOCAL search_path TO public,pg_catalog')


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM work_tasks WHERE mode='agent'")):
        raise RuntimeError('Restore a matching backup to preserve workspace tasks and execution history')
    op.execute('DROP SCHEMA work_checkpoints CASCADE')
    for table in ('work_sandbox_leases', 'work_artifacts', 'work_steps', 'work_inputs'):
        op.drop_table(table)
    op.alter_column('work_tasks', 'create_active_agent_id', existing_type=sa.Uuid(), nullable=False)
    op.drop_index('ix_work_checkpoint_cleanup', table_name='work_tasks')
    for column in (
        'checkpoint_cleanup_after',
        'workspace_files',
        'agent_checkpoint_id',
        'plan',
        'execution_state',
        'configuration',
        'mode',
    ):
        op.drop_column('work_tasks', column)
