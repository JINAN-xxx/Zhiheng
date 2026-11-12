"""Add opt-in platform credits without converting or charging historical usage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0012_platform_credits'
down_revision = '0011_builtin_admin_role'
branch_labels = None
depends_on = None


def _points(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.Numeric(38, 18), nullable=nullable, server_default=sa.text('0') if default else None)


def _created() -> sa.Column:
    return sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    for name in ('limit_tokens', 'used_tokens', 'reserved_tokens'):
        op.alter_column('developer_accounts', name, existing_type=sa.Integer(), type_=sa.BigInteger())
    op.alter_column('developer_minute_buckets', 'tokens', existing_type=sa.Integer(), type_=sa.BigInteger())
    op.add_column('developer_keys', _points('monthly_points_limit', nullable=True))
    op.create_check_constraint(
        'ck_developer_keys_monthly_points_limit',
        'developer_keys',
        'monthly_points_limit IS NULL OR monthly_points_limit >= 0',
    )
    op.add_column(
        'developer_invocations', sa.Column('points_enforced', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        'billing_accounts',
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('0')),
        *(
            _points(name, default=True)
            for name in ('allocated_points', 'reclaimed_points', 'used_points', 'reserved_points')
        ),
        sa.CheckConstraint(
            'allocated_points >= 0 AND reclaimed_points >= 0 AND used_points >= 0 AND reserved_points >= 0'
        ),
    )
    op.create_table(
        'billing_control',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.CheckConstraint('id = 1'),
    )
    op.execute('INSERT INTO billing_control(id,enabled,version) VALUES (1,false,0)')
    op.create_table(
        'billing_rates',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('model_id', sa.Uuid(), sa.ForeignKey('models.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('billing_mode', sa.String(16), nullable=False),
        sa.Column('is_free', sa.Boolean(), nullable=False),
        *(_points(name) for name in ('input_points', 'output_points', 'cache_points', 'search_points')),
        _created(),
        sa.UniqueConstraint('model_id', 'version'),
        sa.CheckConstraint("billing_mode IN ('token','search_unit')"),
        sa.CheckConstraint('input_points >= 0 AND output_points >= 0 AND cache_points >= 0 AND search_points >= 0'),
        sa.CheckConstraint(
            '(is_free AND input_points = 0 AND output_points = 0 AND cache_points = 0 AND search_points = 0) OR (NOT is_free AND input_points + output_points + cache_points + search_points > 0)'
        ),
    )
    op.create_index('ix_billing_rates_model_id', 'billing_rates', ['model_id'])
    op.create_table(
        'billing_commands',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('operator_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('client_request_id', sa.String(128), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('target', sa.String(80), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('result', postgresql.JSONB(), nullable=False),
        sa.Column('reason', sa.String(1000), nullable=False),
        _created(),
        sa.UniqueConstraint('operator_id', 'client_request_id'),
    )
    op.create_table(
        'billing_key_months',
        sa.Column('key_id', sa.Uuid(), sa.ForeignKey('developer_keys.id'), primary_key=True),
        sa.Column('month', sa.String(7), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        _points('used_points', default=True),
        _points('reserved_points', default=True),
        sa.CheckConstraint('used_points >= 0 AND reserved_points >= 0'),
    )
    op.create_table(
        'billing_reservations',
        sa.Column('run_id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('model_id', sa.Uuid(), sa.ForeignKey('models.id'), nullable=False),
        sa.Column('source', sa.String(24), nullable=False),
        sa.Column('operation', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='reserved'),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        _points('reserved_points'),
        _points('charged_points', nullable=True),
        sa.Column('rate_id', sa.Uuid(), sa.ForeignKey('billing_rates.id'), nullable=False),
        sa.Column('rate_version', sa.Integer(), nullable=False),
        sa.Column('rate_snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('request_payload', postgresql.JSONB(), nullable=False),
        sa.Column('usage_snapshot', postgresql.JSONB(), nullable=True),
        sa.Column('key_id', sa.Uuid(), sa.ForeignKey('developer_keys.id'), nullable=True),
        sa.Column('key_month', sa.String(7), nullable=True),
        *(
            sa.Column(name, sa.Integer(), nullable=True)
            for name in ('input_tokens', 'output_tokens', 'cache_tokens', 'search_units', 'reasoning_tokens')
        ),
        _created(),
        sa.CheckConstraint("status IN ('reserved','unknown','settled')"),
        sa.CheckConstraint('reserved_points >= 0 AND (charged_points IS NULL OR charged_points >= 0)'),
    )
    op.create_index('ix_billing_reservations_user_id', 'billing_reservations', ['user_id'])
    op.create_index('ix_billing_reservations_key_id', 'billing_reservations', ['key_id'])
    op.create_table(
        'billing_ledger',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('operator_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=True),
        sa.Column('kind', sa.String(32), nullable=False),
        *(
            _points(name)
            for name in ('amount', 'available_delta', 'reserved_delta', 'available_after', 'reserved_after')
        ),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('billing_reservations.run_id'), nullable=True),
        sa.Column('reason', sa.String(1000), nullable=False),
        _created(),
    )
    op.create_index('ix_billing_ledger_user_id', 'billing_ledger', ['user_id'])
    op.create_index('ix_billing_ledger_run_id', 'billing_ledger', ['run_id'])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            'LOCK TABLE billing_accounts,billing_reservations,billing_key_months,billing_ledger,'
            'billing_commands,billing_rates,billing_control,developer_accounts,developer_minute_buckets,'
            'developer_invocations,developer_keys IN ACCESS EXCLUSIVE MODE'
        )
    )
    history = any(
        connection.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM {name})'))
        for name in (
            'billing_accounts',
            'billing_reservations',
            'billing_key_months',
            'billing_ledger',
            'billing_commands',
            'billing_rates',
        )
    )
    configured = connection.scalar(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM billing_control WHERE enabled OR version <> 0)'
            ' OR EXISTS (SELECT 1 FROM developer_keys WHERE monthly_points_limit IS NOT NULL)'
            ' OR EXISTS (SELECT 1 FROM developer_invocations WHERE points_enforced)'
        )
    )
    if history or configured:
        raise RuntimeError('Cannot downgrade platform credits: credit accounting data or configuration exists')
    overflow = connection.scalar(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM developer_accounts WHERE limit_tokens NOT BETWEEN :low AND :high'
            ' OR used_tokens NOT BETWEEN :low AND :high OR reserved_tokens NOT BETWEEN :low AND :high)'
            ' OR EXISTS (SELECT 1 FROM developer_minute_buckets WHERE tokens NOT BETWEEN :low AND :high)'
        ),
        {'low': -2147483648, 'high': 2147483647},
    )
    if overflow:
        raise RuntimeError('Cannot downgrade platform credits: historical meter counters exceed INT32')
    for name in (
        'billing_ledger',
        'billing_reservations',
        'billing_key_months',
        'billing_commands',
        'billing_rates',
        'billing_control',
        'billing_accounts',
    ):
        op.drop_table(name)
    op.drop_column('developer_invocations', 'points_enforced')
    op.drop_constraint('ck_developer_keys_monthly_points_limit', 'developer_keys', type_='check')
    op.drop_column('developer_keys', 'monthly_points_limit')
    for name in ('limit_tokens', 'used_tokens', 'reserved_tokens'):
        op.alter_column('developer_accounts', name, existing_type=sa.BigInteger(), type_=sa.Integer())
    op.alter_column('developer_minute_buckets', 'tokens', existing_type=sa.BigInteger(), type_=sa.Integer())
