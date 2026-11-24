"""Retire pre-object credentials while retaining their historical billing identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0044_key_object_grants'
down_revision = '0043_workspace_execution'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE developer_keys SET status='revoked', retire_at=NULL, deleted_at=COALESCE(deleted_at, CURRENT_TIMESTAMP), "
        "digest='retired:' || id::text, prefix='', scopes='[]'::jsonb, model_ids='[]'::jsonb"
    )
    for column in ('agent_ids', 'resource_grants', 'create_resource_kinds'):
        op.add_column('developer_keys', sa.Column(column, postgresql.JSONB(), nullable=False, server_default='[]'))
    op.alter_column('developer_keys', 'scopes', server_default='[]')
    op.add_column('developer_keys', sa.Column('grant_family_id', sa.Uuid(), nullable=True))
    op.add_column('developer_resource_grants', sa.Column('creator_family_id', sa.Uuid(), nullable=True))
    op.create_index(
        'ix_developer_resource_grants_creator_family_id', 'developer_resource_grants', ['creator_family_id']
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM developer_keys WHERE deleted_at IS NULL '
            "AND status IN ('active','retiring'))"
        )
    ):
        raise RuntimeError('Revoke object-scoped keys before downgrading their authorization policy')
    op.drop_index('ix_developer_resource_grants_creator_family_id', table_name='developer_resource_grants')
    op.drop_column('developer_resource_grants', 'creator_family_id')
    op.alter_column('developer_keys', 'scopes', server_default='["models:invoke","agents:invoke"]')
    for column in ('grant_family_id', 'create_resource_kinds', 'resource_grants', 'agent_ids'):
        op.drop_column('developer_keys', column)
