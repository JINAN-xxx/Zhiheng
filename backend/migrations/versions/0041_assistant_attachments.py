"""Add compatible attachment summaries to private assistant turns."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0041_assistant_attachments'
down_revision = '0040_assistant_agents'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'assistant_turns',
        sa.Column('attachments', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM assistant_turns WHERE attachments != '[]'::jsonb")):
        raise RuntimeError('Assistant attachment history must be preserved before downgrading')
    op.drop_column('assistant_turns', 'attachments')
