"""Retain key identity and accounting history when removing a credential."""

import sqlalchemy as sa
from alembic import op

revision = '0019_developer_key_deletion'
down_revision = '0018_file_job_cursor'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('developer_keys', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Deletion also marks the key revoked, so removing this metadata cannot
    # reactivate credentials or remove their historical references.
    op.drop_column('developer_keys', 'deleted_at')
