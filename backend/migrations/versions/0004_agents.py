"""Agent drafts/publications and durable reasoning projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004_agents'
down_revision = '0003_skills'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agents',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('publication_count', sa.Integer(), nullable=False),
        sa.Column('published_version', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_agents_owner_id', 'agents', ['owner_id'])
    op.create_table(
        'agent_versions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('agent_id', sa.Uuid(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('draft_version', sa.Integer(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('skills', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('agent_id', 'version', name='uq_agent_publication'),
    )
    op.create_table(
        'agent_run_audits',
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('runtime_runs.id'), primary_key=True),
        sa.Column('agent_id', sa.Uuid(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('config_version', sa.Integer(), nullable=False),
        sa.Column('published_version', sa.Integer()),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('skills', postgresql.JSONB(), nullable=False),
        sa.Column('knowledge', postgresql.JSONB(), nullable=False),
    )
    op.add_column('runtime_runs', sa.Column('reasoning', sa.Text(), nullable=False, server_default=''))
    op.add_column('chat_messages', sa.Column('reasoning', sa.Text(), nullable=False, server_default=''))
    op.add_column('runtime_runs', sa.Column('billing_owner_id', sa.Uuid(), nullable=True))
    op.execute('UPDATE runtime_runs SET billing_owner_id = owner_id')
    op.alter_column('runtime_runs', 'billing_owner_id', nullable=False)
    op.execute("""CREATE FUNCTION reject_agent_version_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'agent versions are immutable' USING ERRCODE = '55000'; END;
        $$""")
    op.execute("""CREATE TRIGGER immutable_agent_versions BEFORE UPDATE OR DELETE ON agent_versions
        FOR EACH ROW EXECUTE FUNCTION reject_agent_version_mutation()""")


def downgrade() -> None:
    op.drop_column('runtime_runs', 'billing_owner_id')
    op.drop_column('runtime_runs', 'reasoning')
    op.drop_column('chat_messages', 'reasoning')
    op.drop_table('agent_run_audits')
    op.drop_table('agent_versions')
    op.execute('DROP FUNCTION reject_agent_version_mutation()')
    op.drop_table('agents')
