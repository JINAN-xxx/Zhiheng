"""Separate platform suspension from the owner's application switch."""

import sqlalchemy as sa
from alembic import op

revision = '0029_application_governance'
down_revision = '0028_document_parsing'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'developer_applications', sa.Column('platform_enabled', sa.Boolean(), nullable=False, server_default=sa.true())
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text('SELECT count(*) FROM developer_applications WHERE NOT platform_enabled AND deleted_at IS NULL')
    ):
        raise RuntimeError('Restore platform application switches before downgrading')
    op.drop_column('developer_applications', 'platform_enabled')
