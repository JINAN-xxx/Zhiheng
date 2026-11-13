"""Retire global sharing; preserve explicit members and make future libraries private."""

import sqlalchemy as sa
from alembic import op

revision = '0023_knowledge_link_only'
down_revision = '0022_knowledge_invitations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE knowledge_bases SET access='private', acl_version=acl_version+1, "
            "invite_epoch=invite_epoch+1, updated_at=CURRENT_TIMESTAMP WHERE access='public'"
        )
    )
    op.create_check_constraint('ck_knowledge_bases_link_only', 'knowledge_bases', "access = 'private'")


def downgrade() -> None:
    # Downgrading must not silently reopen libraries to all accounts.
    op.drop_constraint('ck_knowledge_bases_link_only', 'knowledge_bases', type_='check')
