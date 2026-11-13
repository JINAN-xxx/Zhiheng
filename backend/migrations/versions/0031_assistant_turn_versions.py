"""Record publication provenance on each assistant turn while sessions follow the latest version."""

import sqlalchemy as sa
from alembic import op

revision = '0031_assistant_turn_versions'
down_revision = '0030_assistant_conversations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assistant_turns', sa.Column('published_version', sa.Integer(), nullable=True))
    op.add_column('assistant_turns', sa.Column('agent_name', sa.String(100), nullable=True))
    op.execute(
        sa.text(
            "UPDATE assistant_turns t SET published_version=c.published_version, agent_name=c.config->>'name' FROM assistant_conversations c WHERE c.id=t.conversation_id"
        )
    )
    op.alter_column('assistant_turns', 'published_version', existing_type=sa.Integer(), nullable=False)
    op.alter_column('assistant_turns', 'agent_name', existing_type=sa.String(100), nullable=False)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM assistant_turns')):
        raise RuntimeError('Preserve per-turn assistant provenance before downgrading')
    op.drop_column('assistant_turns', 'agent_name')
    op.drop_column('assistant_turns', 'published_version')
