"""Opaque, single-use sharing invitations and policy invalidation epochs."""

import sqlalchemy as sa
from alembic import op

revision = '0022_knowledge_invitations'
down_revision = '0021_knowledge_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('knowledge_bases', sa.Column('invite_epoch', sa.Integer(), nullable=False, server_default='1'))
    op.create_table(
        'knowledge_invitations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('base_id', sa.Uuid(), sa.ForeignKey('knowledge_bases.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('epoch', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_by', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('base_id', 'request_id'),
    )
    op.create_index('ix_knowledge_invitations_base_id', 'knowledge_invitations', ['base_id'])


def downgrade() -> None:
    op.drop_table('knowledge_invitations')
    op.drop_column('knowledge_bases', 'invite_epoch')
