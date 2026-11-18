"""Retire task-local execution opt-in; current account grants control tool access."""

from alembic import op

revision = '0043_workspace_execution'
down_revision = '0042_workspace_merge'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE work_tasks SET configuration = configuration - 'allow_code_execution' WHERE mode = 'agent' AND configuration ? 'allow_code_execution'"
    )


def downgrade() -> None:
    # Returning to the old opt-in contract must not silently enable execution.
    op.execute(
        "UPDATE work_tasks SET configuration = configuration || jsonb_build_object('allow_code_execution', false) WHERE mode = 'agent' AND NOT configuration ? 'allow_code_execution'"
    )
