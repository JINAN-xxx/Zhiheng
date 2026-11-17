"""Record capacity evidence without changing accepted model bindings or prices."""

import sqlalchemy as sa
from alembic import op

revision = '0037_model_capacity_note'
down_revision = '0036_chat_runtime_cleanup'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('models', sa.Column('capacity_note', sa.Text(), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('models', 'capacity_note')
