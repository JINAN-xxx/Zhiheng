"""Join the independently deployed workspace and assistant-attachment histories.

Keep both existing revisions unchanged so either deployed schema runs only its
missing branch before reaching this shared application revision.
"""

revision = '0042_workspace_merge'
down_revision = ('0041_work_agent', '0041_assistant_attachments')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
