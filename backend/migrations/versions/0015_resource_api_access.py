"""Add restricted resource capabilities and permanent application admission facts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0015_resource_api_access'
down_revision = '0014_personal_skill_ownership'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'developer_keys',
        sa.Column('scopes', postgresql.JSONB(), nullable=False, server_default='["models:invoke","agents:invoke"]'),
    )
    op.add_column('developer_keys', sa.Column('resource_rpm_limit', sa.Integer(), nullable=False, server_default='120'))
    op.create_check_constraint(
        'ck_key_resource_rpm', 'developer_keys', 'resource_rpm_limit > 0 AND resource_rpm_limit <= 120'
    )
    op.add_column(
        'developer_applications', sa.Column('source_file_limit', sa.Integer(), nullable=False, server_default='1000')
    )
    op.add_column(
        'developer_applications',
        sa.Column('source_byte_limit', sa.BigInteger(), nullable=False, server_default='1073741824'),
    )
    op.create_check_constraint(
        'ck_application_source_limits', 'developer_applications', 'source_file_limit > 0 AND source_byte_limit > 0'
    )
    op.create_table(
        'developer_resource_grants',
        sa.Column('application_id', sa.Uuid(), sa.ForeignKey('developer_applications.id'), primary_key=True),
        sa.Column('kind', sa.String(24), primary_key=True),
        sa.Column('resource_id', sa.Uuid(), primary_key=True),
        sa.Column('actions', postgresql.JSONB(), nullable=False),
        sa.Column('resource_owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('ownership_stamp', sa.String(80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('file','knowledge_base','skill')"),
    )
    op.create_table(
        'developer_source_enrollments',
        sa.Column('application_id', sa.Uuid(), sa.ForeignKey('developer_applications.id'), primary_key=True),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('purged_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('size >= 0'),
    )
    op.create_table(
        'developer_resource_minute_buckets',
        sa.Column('key_id', sa.Uuid(), sa.ForeignKey('developer_keys.id'), primary_key=True),
        sa.Column('minute', sa.Integer(), primary_key=True),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.CheckConstraint('requests >= 0'),
    )
    op.create_table(
        'developer_api_requests',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('application_id', sa.Uuid(), sa.ForeignKey('developer_applications.id'), nullable=False),
        sa.Column('key_id', sa.Uuid(), sa.ForeignKey('developer_keys.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('operation', sa.String(80), nullable=False),
        sa.Column('idempotency_key', sa.Uuid(), nullable=False),
        sa.Column('semantic_hash', sa.String(64), nullable=False),
        sa.Column('required_scope', sa.String(32), nullable=False),
        sa.Column('resources', postgresql.JSONB(), nullable=False),
        sa.Column('result_kind', sa.String(32)),
        sa.Column('result_id', sa.Uuid()),
        sa.Column('snapshot', postgresql.JSONB()),
        sa.Column('snapshot_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('application_id', 'operation', 'idempotency_key'),
    )
    op.create_index('ix_developer_api_requests_application_id', 'developer_api_requests', ['application_id'])


def downgrade() -> None:
    connection = op.get_bind()
    for name in (
        'developer_resource_grants',
        'developer_source_enrollments',
        'developer_resource_minute_buckets',
        'developer_api_requests',
    ):
        if connection.scalar(sa.text(f'SELECT EXISTS(SELECT 1 FROM {name})')):
            raise RuntimeError('Cannot downgrade populated resource API facts')
    if connection.scalar(
        sa.text("""SELECT EXISTS(SELECT 1 FROM developer_keys
        WHERE scopes <> '["models:invoke","agents:invoke"]'::jsonb OR resource_rpm_limit <> 120)""")
    ):
        raise RuntimeError('Cannot downgrade resource Key capabilities')
    if connection.scalar(
        sa.text(
            'SELECT EXISTS(SELECT 1 FROM developer_applications WHERE source_file_limit<>1000 OR source_byte_limit<>1073741824)'
        )
    ):
        raise RuntimeError('Cannot downgrade configured resource quotas')
    op.drop_table('developer_api_requests')
    op.drop_table('developer_resource_minute_buckets')
    op.drop_table('developer_source_enrollments')
    op.drop_table('developer_resource_grants')
    op.drop_constraint('ck_application_source_limits', 'developer_applications', type_='check')
    op.drop_column('developer_applications', 'source_byte_limit')
    op.drop_column('developer_applications', 'source_file_limit')
    op.drop_constraint('ck_key_resource_rpm', 'developer_keys', type_='check')
    op.drop_column('developer_keys', 'resource_rpm_limit')
    op.drop_column('developer_keys', 'scopes')
