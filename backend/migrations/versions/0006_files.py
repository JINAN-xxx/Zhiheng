"""Managed files, ACLs, immutable attachment snapshots and maintenance facts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0006_files'
down_revision = '0005_work'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'files',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('storage_key', sa.String(32), nullable=False),
        sa.Column('original_filename', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('mime', sa.String(100), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('uploader_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('access', sa.String(16), nullable=False),
        sa.Column('uploader_access_enabled', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('acl_version', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('retention_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=True),
        sa.Column('storage_status', sa.String(16), nullable=False),
        sa.Column('parser_version', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('storage_key'),
    )
    op.create_index('ix_files_status', 'files', ['status'], unique=False)
    op.create_index('ix_files_owner_id', 'files', ['owner_id'], unique=False)
    op.create_index('ix_files_expires_at', 'files', ['expires_at'], unique=False)
    op.create_index('ix_files_source_file_id', 'files', ['source_file_id'], unique=False)
    op.create_table(
        'file_grants',
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), primary_key=True, nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), primary_key=True, nullable=False),
        sa.Column('effect', sa.String(8), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'file_references',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('resource_type', sa.String(32), nullable=False),
        sa.Column('resource_id', sa.Uuid(), nullable=False),
        sa.Column('retain_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('file_id', 'resource_type', 'resource_id'),
    )
    op.create_index('ix_file_references_file_id', 'file_references', ['file_id'], unique=False)
    op.create_table(
        'file_audits',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('details', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_file_audits_file_id', 'file_audits', ['file_id'], unique=False)
    op.create_table(
        'file_policy',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('reminder_days', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
    )
    op.create_table(
        'file_reminders',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column('read_by', sa.Uuid(), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expiry_key', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('file_id', 'kind', 'expiry_key'),
    )
    op.create_index('ix_file_reminders_owner_id', 'file_reminders', ['owner_id'], unique=False)
    op.create_table(
        'file_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scanned', sa.Integer(), nullable=False),
        sa.Column('changed', sa.Integer(), nullable=False),
        sa.Column('errors', postgresql.JSONB(), nullable=False),
        sa.Column('cursor', sa.String(64), nullable=False),
    )
    op.create_table(
        'file_retention_policies',
        sa.Column('resource_type', sa.String(32), primary_key=True, nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('remark', sa.String(500), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'file_reconcile_issues',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('job_id', sa.Uuid(), sa.ForeignKey('file_jobs.id'), nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=True),
        sa.Column('identity_key', sa.String(100), nullable=False),
        sa.Column('storage_key', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('detail', sa.String(500), nullable=False),
        sa.Column('expected_size', sa.Integer(), nullable=True),
        sa.Column('actual_size', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('identity_key'),
    )
    op.add_column('chat_conversations', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('chat_conversations', sa.Column('archived', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('chat_conversations', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('models', sa.Column('vision_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('chat_messages', sa.Column('attachments', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.add_column(
        'chat_messages', sa.Column('attachment_inputs', postgresql.JSONB(), nullable=False, server_default='[]')
    )


def downgrade() -> None:
    op.drop_column('chat_conversations', 'deleted_at')
    op.drop_column('chat_conversations', 'archived')
    op.drop_column('chat_conversations', 'version')
    op.drop_column('chat_messages', 'attachment_inputs')
    op.drop_column('chat_messages', 'attachments')
    op.drop_column('models', 'vision_enabled')
    op.drop_table('file_reconcile_issues')
    op.drop_table('file_retention_policies')
    op.drop_table('file_jobs')
    op.drop_table('file_reminders')
    op.drop_table('file_policy')
    op.drop_table('file_audits')
    op.drop_table('file_references')
    op.drop_table('file_grants')
    op.drop_table('files')
