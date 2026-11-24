"""Persist issue requests and developer request outcomes separately from usage."""

import sqlalchemy as sa
from alembic import op

revision = '0045_developer_key_controls'
down_revision = '0044_key_object_grants'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('developer_keys', sa.Column('issue_request_id', sa.Uuid()))
    op.create_unique_constraint(
        'uq_developer_key_issue_request', 'developer_keys', ['application_id', 'issue_request_id']
    )
    op.add_column('developer_invocations', sa.Column('quota_reserved_tokens', sa.Integer()))
    op.create_table(
        'developer_public_request_logs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('application_id', sa.Uuid(), sa.ForeignKey('developer_applications.id'), nullable=False),
        sa.Column('key_id', sa.Uuid(), sa.ForeignKey('developer_keys.id'), nullable=False),
        sa.Column('method', sa.String(12), nullable=False),
        sa.Column('route', sa.String(200), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(80)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        'ix_developer_public_request_logs_application_id', 'developer_public_request_logs', ['application_id']
    )


def downgrade() -> None:
    op.drop_table('developer_public_request_logs')
    op.drop_column('developer_invocations', 'quota_reserved_tokens')
    op.drop_constraint('uq_developer_key_issue_request', 'developer_keys')
    op.drop_column('developer_keys', 'issue_request_id')
