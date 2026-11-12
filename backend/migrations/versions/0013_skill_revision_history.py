"""Keep skill identities stable across immutable package revisions and restores."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0013_skill_revision_history'
down_revision = '0012_platform_credits'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('skill_revisions_skill_id_key', 'skill_revisions', type_='unique')
    op.create_index('ix_skill_revisions_skill_id', 'skill_revisions', ['skill_id'])
    op.add_column('skill_drafts', sa.Column('target_skill_id', sa.Uuid(), sa.ForeignKey('skills.id')))
    op.add_column('skill_drafts', sa.Column('target_version', sa.Integer()))
    op.add_column('skill_drafts', sa.Column('action', sa.String(16), server_default='install', nullable=False))
    op.add_column('skill_drafts', sa.Column('confirmed_revision_id', sa.Uuid(), sa.ForeignKey('skill_revisions.id')))
    op.add_column('skill_drafts', sa.Column('confirmed_info', postgresql.JSONB()))
    # Existing confirmations have exactly one immutable revision before this upgrade.
    # Freeze their current response once, before later updates can move the pointer.
    op.execute("""UPDATE skill_drafts d SET confirmed_revision_id=r.id,
        confirmed_info=jsonb_build_object(
            'id',s.id,'slug',s.slug,'name',r.name,'description',r.description,
            'package_version',r.package_version,'version',s.version,'status',s.status,
            'source',s.source,'revision_id',r.id,'content_hash',r.content_hash,
            'file_count',r.file_count,'total_bytes',r.total_bytes,
            'created_at',s.created_at,'updated_at',s.updated_at)
        FROM skills s JOIN skill_revisions r ON r.id=s.revision_id
        WHERE d.confirmed_skill_id=s.id""")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM skill_revisions GROUP BY skill_id HAVING count(*)>1)')):
        raise RuntimeError('Cannot downgrade skill history with multiple revisions; preserve the database at 0013')
    if connection.scalar(
        sa.text("""SELECT EXISTS(SELECT 1 FROM skill_drafts
        WHERE target_skill_id IS NOT NULL OR action<>'install' OR confirmed_info IS NOT NULL)""")
    ):
        raise RuntimeError(
            'Cannot downgrade frozen skill confirmations or import targets without losing lifecycle data'
        )
    op.drop_column('skill_drafts', 'confirmed_info')
    op.drop_column('skill_drafts', 'confirmed_revision_id')
    op.drop_column('skill_drafts', 'action')
    op.drop_column('skill_drafts', 'target_version')
    op.drop_column('skill_drafts', 'target_skill_id')
    op.drop_index('ix_skill_revisions_skill_id', table_name='skill_revisions')
    op.create_unique_constraint('skill_revisions_skill_id_key', 'skill_revisions', ['skill_id'])
