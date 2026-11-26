"""Publish selected MCP tools to developer applications."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0052_mcp_gateway'
down_revision = '0051_operation_permissions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('developer_keys', sa.Column('mcp_tool_ids', JSONB(), nullable=False, server_default='[]'))
    op.create_table(
        'mcp_publications',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('application_id', sa.Uuid(), sa.ForeignKey('developer_applications.id'), nullable=False),
        sa.Column('server_id', sa.Uuid(), sa.ForeignKey('mcp_servers.id'), nullable=False),
        sa.Column('source_synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('definition', JSONB(), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_mcp_publications_application_id', 'mcp_publications', ['application_id'])
    op.create_index(
        'uq_mcp_publication_active_name',
        'mcp_publications',
        ['application_id', 'name'],
        unique=True,
        postgresql_where=sa.text('withdrawn_at IS NULL'),
    )
    op.alter_column('mcp_calls', 'run_id', nullable=True)
    op.add_column('mcp_calls', sa.Column('application_id', sa.Uuid()))
    op.add_column('mcp_calls', sa.Column('key_id', sa.Uuid()))
    op.create_check_constraint(
        'ck_mcp_call_origin',
        'mcp_calls',
        '(run_id IS NOT NULL AND application_id IS NULL AND key_id IS NULL) OR '
        '(run_id IS NULL AND application_id IS NOT NULL AND key_id IS NOT NULL)',
    )


def downgrade() -> None:
    active = op.get_bind().scalar(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM mcp_publications) OR '
            'EXISTS (SELECT 1 FROM mcp_calls WHERE run_id IS NULL) OR '
            "EXISTS (SELECT 1 FROM developer_keys WHERE mcp_tool_ids != '[]'::jsonb)"
        )
    )
    if active:
        raise RuntimeError('Restore a reviewed backup to retain MCP publications, keys and audit records.')
    op.drop_constraint('ck_mcp_call_origin', 'mcp_calls', type_='check')
    op.drop_column('mcp_calls', 'key_id')
    op.drop_column('mcp_calls', 'application_id')
    op.alter_column('mcp_calls', 'run_id', nullable=False)
    op.drop_table('mcp_publications')
    op.drop_column('developer_keys', 'mcp_tool_ids')
