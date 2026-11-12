"""Persist file purpose independently of temporary API response snapshots."""

import sqlalchemy as sa
from alembic import op

revision = '0016_file_purpose'
down_revision = '0015_resource_api_access'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('files', sa.Column('purpose', sa.String(64), nullable=False, server_default='general'))


def downgrade() -> None:
    op.drop_column('files', 'purpose')
