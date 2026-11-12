"""Fit both file-record and object cursors in a reconciliation checkpoint."""

import sqlalchemy as sa
from alembic import op

revision = '0018_file_job_cursor'
down_revision = '0017_chat_context_memory'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('file_jobs', 'cursor', existing_type=sa.String(64), type_=sa.String(128), existing_nullable=False)


def downgrade() -> None:
    op.alter_column('file_jobs', 'cursor', existing_type=sa.String(128), type_=sa.String(64), existing_nullable=False)
