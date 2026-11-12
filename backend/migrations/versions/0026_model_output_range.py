"""Allow model output budgets above the former platform cap without changing existing values."""

import sqlalchemy as sa
from alembic import op

revision = '0026_model_output_range'
down_revision = '0025_product_closure'
branch_labels = None
depends_on = None
MAX_INT32 = 2147483647


def upgrade() -> None:
    op.alter_column(
        'models', 'max_output_tokens', existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False
    )


def downgrade() -> None:
    maximum = op.get_bind().scalar(sa.text('SELECT max(max_output_tokens) FROM models'))
    if maximum is not None and maximum > MAX_INT32:
        raise RuntimeError('Reduce model output budgets before restoring 32-bit storage')
    op.alter_column(
        'models', 'max_output_tokens', existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False
    )
