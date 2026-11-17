"""Retire unused lifetime quota and duplicate manual-runtime diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = '0039_retired_runtime_fields'
down_revision = '0038_skill_receipt_payload'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for constraint in sa.inspect(op.get_bind()).get_check_constraints('developer_accounts'):
        if 'limit_tokens' in constraint['sqltext']:
            op.drop_constraint(constraint['name'], 'developer_accounts', type_='check')
    op.create_check_constraint(
        'ck_usage_accounts_nonnegative', 'developer_accounts', 'used_tokens >= 0 AND reserved_tokens >= 0'
    )
    op.drop_column('developer_accounts', 'limit_tokens')
    op.drop_column('developer_accounts', 'quota_version')
    for column in ('summary_version', 'estimated_tokens', 'input_budget', 'dropped_turns'):
        op.drop_column('chat_context_snapshots', column)
    op.drop_column('knowledge_graph_attempts', 'elapsed_seconds')


def downgrade() -> None:
    raise RuntimeError('Retired quota and manual-runtime values cannot be reconstructed; restore a database backup')
