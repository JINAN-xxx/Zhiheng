"""Initialize official chat checkpoints and effective-history references.

The DDL is frozen from langgraph-checkpoint-postgres 3.2.0 MIGRATIONS 0..9.
Its three CONCURRENTLY indexes become ordinary indexes inside this explicit
Alembic transaction. Runtime saver construction never runs SDK setup.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0035_chat_checkpoints'
down_revision = '0034_permission_tree'
branch_labels = None
depends_on = None

CHECKPOINT_MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        parent_checkpoint_id TEXT,
        type TEXT,
        checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    )""",
    """CREATE TABLE IF NOT EXISTS checkpoint_blobs (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL,
        version TEXT NOT NULL,
        type TEXT NOT NULL,
        blob BYTEA,
        PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
    )""",
    """CREATE TABLE IF NOT EXISTS checkpoint_writes (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        channel TEXT NOT NULL,
        type TEXT,
        blob BYTEA NOT NULL,
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    )""",
    'ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP NOT NULL',
    'SELECT 1',
    'CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id)',
    'CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)',
    'CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)',
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''",
)


def upgrade() -> None:
    op.add_column('chat_context_states', sa.Column('agent_checkpoint_id', sa.Text(), nullable=True))
    op.add_column('chat_context_states', sa.Column('agent_source_hash', sa.String(64), nullable=True))
    op.add_column('chat_context_snapshots', sa.Column('agent_context', postgresql.JSONB(), nullable=True))
    op.add_column('chat_context_snapshots', sa.Column('agent_checkpoint_id', sa.Text(), nullable=True))
    op.execute('CREATE SCHEMA chat_checkpoints')
    op.execute('REVOKE ALL ON SCHEMA chat_checkpoints FROM PUBLIC')
    op.execute('SET LOCAL search_path TO chat_checkpoints,pg_catalog')
    for version, statement in enumerate(CHECKPOINT_MIGRATIONS):
        op.execute(sa.text(statement))
        op.execute(sa.text('INSERT INTO checkpoint_migrations (v) VALUES (:version)').bindparams(version=version))
    op.execute('SET LOCAL search_path TO public,pg_catalog')


def downgrade() -> None:
    op.execute('DROP SCHEMA chat_checkpoints CASCADE')
    op.drop_column('chat_context_snapshots', 'agent_checkpoint_id')
    op.drop_column('chat_context_snapshots', 'agent_context')
    op.drop_column('chat_context_states', 'agent_source_hash')
    op.drop_column('chat_context_states', 'agent_checkpoint_id')
