"""Keep the currently published skill separate from the personal draft."""

import sqlalchemy as sa
from alembic import op

revision = '0047_platform_skills'
down_revision = '0046_assistant_plaza'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('skills', sa.Column('platform_revision_id', sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.execute('LOCK TABLE skills IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM skills WHERE platform_revision_id IS NOT NULL)')):
        raise RuntimeError('Platform skill publication data must be preserved. Withdraw it before downgrading.')
    op.drop_column('skills', 'platform_revision_id')
