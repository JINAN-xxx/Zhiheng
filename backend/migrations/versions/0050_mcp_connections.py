"""Register MCP connections and retain redacted invocation records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0050_mcp_connections'
down_revision = '0049_settings_permissions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mcp_servers',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('endpoint', sa.String(512), nullable=False),
        sa.Column('encrypted_token', sa.Text()),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True)),
        sa.Column('tools', JSONB(), nullable=False),
    )
    op.create_table(
        'mcp_calls',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('server_id', sa.Uuid(), sa.ForeignKey('mcp_servers.id'), nullable=False),
        sa.Column('caller_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('tool_name', sa.String(120), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('error_code', sa.String(80)),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mcp_calls_server_created', 'mcp_calls', ['server_id', 'created_at'])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text('SELECT EXISTS (SELECT 1 FROM mcp_servers)')):
        raise RuntimeError('Back up and remove MCP registrations before downgrading.')
    op.drop_table('mcp_calls')
    op.drop_table('mcp_servers')
