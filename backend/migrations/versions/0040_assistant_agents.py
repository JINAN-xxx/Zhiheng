"""Latest-config assistants, official Agent memory and independently accounted summaries.

Existing Run input, audit configuration, usage and financial facts are preserved.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0040_assistant_agents'
down_revision = '0039_retired_runtime_fields'
branch_labels = None
depends_on = None

# Frozen from the already locked official langgraph-checkpoint-postgres 3.2.0.
CHECKPOINT_MIGRATIONS = (
    'CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)',
    "CREATE TABLE IF NOT EXISTS checkpoints (\n        thread_id TEXT NOT NULL,\n        checkpoint_ns TEXT NOT NULL DEFAULT '',\n        checkpoint_id TEXT NOT NULL,\n        parent_checkpoint_id TEXT,\n        type TEXT,\n        checkpoint JSONB NOT NULL,\n        metadata JSONB NOT NULL DEFAULT '{}',\n        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)\n    )",
    "CREATE TABLE IF NOT EXISTS checkpoint_blobs (\n        thread_id TEXT NOT NULL,\n        checkpoint_ns TEXT NOT NULL DEFAULT '',\n        channel TEXT NOT NULL,\n        version TEXT NOT NULL,\n        type TEXT NOT NULL,\n        blob BYTEA,\n        PRIMARY KEY (thread_id, checkpoint_ns, channel, version)\n    )",
    "CREATE TABLE IF NOT EXISTS checkpoint_writes (\n        thread_id TEXT NOT NULL,\n        checkpoint_ns TEXT NOT NULL DEFAULT '',\n        checkpoint_id TEXT NOT NULL,\n        task_id TEXT NOT NULL,\n        idx INTEGER NOT NULL,\n        channel TEXT NOT NULL,\n        type TEXT,\n        blob BYTEA NOT NULL,\n        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)\n    )",
    'ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP NOT NULL',
    'SELECT 1',
    'CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id)',
    'CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)',
    'CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)',
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''",
)


def upgrade() -> None:
    connection = op.get_bind()
    active = connection.scalar(
        sa.text("""SELECT count(*) FROM runtime_runs
        WHERE resource_type IN ('agent_chat','agent_preview','agent_invocation','work_turn')
          AND status IN ('pending','running','cancel_requested')""")
    )
    preparing = connection.scalar(
        sa.text("""SELECT count(*) FROM knowledge_retrieval_intents
        WHERE resource_type IN ('agent_chat','agent_preview','agent_invocation','work_turn')
          AND status NOT IN ('admitted','completed','failed','cancelled','interrupted')""")
    )
    if active or preparing:
        raise RuntimeError(
            'Finish or explicitly cancel assistant/Work Runs and knowledge preparations before upgrading'
        )
    missing = connection.scalar(
        sa.text("""SELECT count(*) FROM assistant_turns t
        JOIN assistant_conversations c ON c.id=t.conversation_id
        LEFT JOIN agent_versions v ON v.agent_id=c.agent_id AND v.version=t.published_version
        WHERE v.id IS NULL""")
    )
    if missing:
        raise RuntimeError(
            'Historical assistant turns lack their original configuration; restore the exact audit source first'
        )
    op.add_column('agents', sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('agents', sa.Column('enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("""UPDATE agents a SET enabled=(published_version IS NOT NULL),
        enabled_at=(SELECT max(created_at) FROM agent_versions v WHERE v.agent_id=a.id)""")
    # Preserve the accepted configuration and skill facts before retiring the version catalog.
    op.execute("""INSERT INTO agent_run_audits
        (run_id,agent_id,owner_id,config_version,published_version,config,skills,knowledge)
        SELECT t.run_id,c.agent_id,t.owner_id,v.draft_version,t.published_version,v.config,v.skills,t.sources
        FROM assistant_turns t JOIN assistant_conversations c ON c.id=t.conversation_id
        JOIN agent_versions v ON v.agent_id=c.agent_id AND v.version=t.published_version
        ON CONFLICT (run_id) DO NOTHING""")
    op.add_column('agent_run_audits', sa.Column('execution', postgresql.JSONB(), nullable=True))
    op.add_column('agent_run_audits', sa.Column('checkpoint_id', sa.Text(), nullable=True))
    # Existing conversations remain readable; only newly created conversations receive version 1.
    op.add_column('assistant_conversations', sa.Column('runtime_version', sa.Integer(), nullable=True))
    op.alter_column('assistant_conversations', 'runtime_version', server_default='1')
    op.add_column('assistant_conversations', sa.Column('checkpoint_id', sa.Text(), nullable=True))
    op.add_column('assistant_conversations', sa.Column('memory_run_id', sa.Uuid(), nullable=True))
    op.add_column(
        'assistant_conversations', sa.Column('checkpoint_cleanup_after', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        'ix_assistant_checkpoint_cleanup',
        'assistant_conversations',
        ['checkpoint_cleanup_after', 'id'],
        postgresql_where=sa.text('deleted_at IS NOT NULL'),
    )
    op.create_table(
        'assistant_summaries',
        sa.Column('parent_run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), primary_key=True),
        sa.Column('parent_attempt_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), nullable=False, unique=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('assistant_conversations.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
    )
    op.create_index('ix_assistant_summaries_conversation_id', 'assistant_summaries', ['conversation_id'])
    op.create_index('ix_assistant_summaries_owner_id', 'assistant_summaries', ['owner_id'])
    # Work retains its already frozen member input; only the misleading column name changes.
    for table in ('work_members', 'work_messages'):
        op.alter_column(table, 'published_version', new_column_name='agent_revision')
    op.execute("UPDATE agents SET config=config-'history_rounds'")
    op.execute("UPDATE assistant_conversations SET config=config-'history_rounds'")
    for table, columns in (
        ('agent_run_audits', ('config_version', 'published_version')),
        ('assistant_conversations', ('published_version',)),
        ('assistant_turns', ('published_version',)),
        ('agents', ('publication_count', 'published_version')),
    ):
        for column in columns:
            op.drop_column(table, column)
    op.drop_table('agent_versions')
    op.execute('DROP FUNCTION reject_agent_version_mutation()')
    op.execute('CREATE SCHEMA assistant_checkpoints')
    op.execute('REVOKE ALL ON SCHEMA assistant_checkpoints FROM PUBLIC')
    op.execute('SET LOCAL search_path TO assistant_checkpoints,pg_catalog')
    for version, statement in enumerate(CHECKPOINT_MIGRATIONS):
        op.execute(sa.text(statement))
        op.execute(sa.text('INSERT INTO checkpoint_migrations (v) VALUES (:version)').bindparams(version=version))
    op.execute('SET LOCAL search_path TO public,pg_catalog')


def downgrade() -> None:
    raise RuntimeError(
        'Restore the pre-upgrade database backup and matching runtime; retired publication history cannot be reconstructed'
    )
