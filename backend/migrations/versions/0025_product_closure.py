"""Add durable creation identities and Token governance without changing existing balances."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0025_product_closure'
down_revision = '0024_knowledge_rerank_required'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('developer_accounts', sa.Column('quota_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('developer_invocations', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('agents', sa.Column('create_client_request_id', sa.Uuid(), nullable=True))
    op.add_column('agents', sa.Column('create_fingerprint', sa.String(64), nullable=True))
    op.create_unique_constraint('uq_agent_create_request', 'agents', ['owner_id', 'create_client_request_id'])
    op.create_table(
        'billing_token_commands',
        sa.Column('actor_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), primary_key=True),
        sa.Column('operation', sa.String(32), primary_key=True),
        sa.Column('request_id', sa.String(128), primary_key=True),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('reason', sa.String(1000), nullable=False),
        sa.Column('result', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('billing_token_commands')
    op.drop_constraint('uq_agent_create_request', 'agents', type_='unique')
    op.drop_column('agents', 'create_fingerprint')
    op.drop_column('agents', 'create_client_request_id')
    op.drop_column('developer_invocations', 'version')
    op.drop_column('developer_accounts', 'quota_version')
