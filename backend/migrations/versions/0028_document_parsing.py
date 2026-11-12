"""Add optional document parsing policy and resumable remote parse tasks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0028_document_parsing'
down_revision = '0027_default_work_skills'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'document_parse_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
    )
    op.execute("INSERT INTO document_parse_settings (id,version,config) VALUES (1,1,'{}'::jsonb)")
    op.create_table(
        'document_parse_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('parent_id', sa.Uuid(), sa.ForeignKey('knowledge_jobs.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('source_sha256', sa.String(64), nullable=False),
        sa.Column('source_version', sa.Integer(), nullable=False),
        sa.Column('source_acl_version', sa.Integer(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('engine', sa.String(16), nullable=False),
        sa.Column('phase', sa.String(24), nullable=False),
        sa.Column('task_id', sa.String(256)),
        sa.Column('upstream_file_id', sa.String(256)),
        sa.Column('artifact_id', sa.Uuid(), sa.ForeignKey('files.id')),
        sa.Column('engine_version', sa.String(80)),
        sa.Column('parser_version', sa.String(64)),
        sa.Column('token', sa.Uuid()),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('next_poll_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=False),
        sa.Column('error_code', sa.String(64)),
        sa.Column('error_message', sa.String(300)),
        sa.Column('history', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('parent_id', 'file_id', 'source_sha256'),
    )
    for column in ('parent_id', 'file_id', 'phase', 'next_poll_at'):
        op.create_index('ix_document_parse_jobs_' + column, 'document_parse_jobs', [column])


def downgrade() -> None:
    op.drop_table('document_parse_jobs')
    op.drop_table('document_parse_settings')
