"""Restore identity administration, system records and published announcements."""

import sqlalchemy as sa
from alembic import op

revision = '0010_admin_console'
down_revision = '0009_message_actions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'identity_departments',
        sa.Column('parent_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('leader', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['identity_departments.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_identity_departments_parent_id'), 'identity_departments', ['parent_id'], unique=False)
    op.create_table(
        'identity_menus',
        sa.Column('parent_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('path', sa.String(length=200), nullable=False),
        sa.Column('permission', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_visible', sa.Boolean(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['identity_menus.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('path'),
    )
    op.create_index(op.f('ix_identity_menus_parent_id'), 'identity_menus', ['parent_id'], unique=False)
    op.create_table(
        'identity_posts',
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('remark', sa.String(length=2000), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_table(
        'identity_roles',
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('remark', sa.String(length=2000), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_table(
        'operations_configs',
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('value', sa.String(length=1000), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('remark', sa.String(length=500), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('version >= 1', name='ck_operations_config_version'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_table(
        'operations_dictionaries',
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('remark', sa.String(length=500), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('version >= 1', name='ck_operations_dictionary_version'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_table(
        'operations_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('action', sa.String(length=80), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('resource_type', sa.String(length=80), nullable=False),
        sa.Column('resource_id', sa.String(length=80), nullable=True),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('login', 'operation')", name='ck_operations_log_kind'),
        sa.CheckConstraint("outcome IN ('success', 'failed')", name='ck_operations_log_outcome'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_operations_logs_created_at'), 'operations_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_operations_logs_kind'), 'operations_logs', ['kind'], unique=False)
    op.create_index(op.f('ix_operations_logs_outcome'), 'operations_logs', ['outcome'], unique=False)
    op.create_index(op.f('ix_operations_logs_username'), 'operations_logs', ['username'], unique=False)
    op.create_table(
        'operations_notices',
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('remark', sa.String(length=500), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('notice', 'announcement')", name='ck_operations_notice_kind'),
        sa.CheckConstraint("status IN ('draft', 'published')", name='ck_operations_notice_status'),
        sa.CheckConstraint('version >= 1', name='ck_operations_notice_version'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_operations_notices_status'), 'operations_notices', ['status'], unique=False)
    op.create_table(
        'operations_dictionary_items',
        sa.Column('dictionary_id', sa.Uuid(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('value', sa.String(length=200), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('remark', sa.String(length=500), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('version >= 1', name='ck_operations_item_version'),
        sa.ForeignKeyConstraint(['dictionary_id'], ['operations_dictionaries.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dictionary_id', 'value', name='uq_operations_dictionary_item_value'),
    )
    op.create_index(
        op.f('ix_operations_dictionary_items_dictionary_id'),
        'operations_dictionary_items',
        ['dictionary_id'],
        unique=False,
    )
    op.create_table(
        'identity_user_posts',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('post_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['identity_posts.id']),
        sa.ForeignKeyConstraint(['user_id'], ['identity_users.id']),
        sa.PrimaryKeyConstraint('user_id', 'post_id'),
    )
    op.create_table(
        'identity_user_roles',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('role_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['identity_roles.id']),
        sa.ForeignKeyConstraint(['user_id'], ['identity_users.id']),
        sa.PrimaryKeyConstraint('user_id', 'role_id'),
    )
    op.add_column(
        'identity_sessions', sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False)
    )
    op.add_column('identity_sessions', sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.add_column('identity_sessions', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint('uq_identity_sessions_id', 'identity_sessions', ['id'])
    op.add_column(
        'identity_users', sa.Column('display_name', sa.String(length=100), server_default=sa.text("''"), nullable=False)
    )
    op.add_column(
        'identity_users', sa.Column('email', sa.String(length=254), server_default=sa.text("''"), nullable=False)
    )
    op.add_column(
        'identity_users', sa.Column('phone', sa.String(length=40), server_default=sa.text("''"), nullable=False)
    )
    op.add_column('identity_users', sa.Column('department_id', sa.Uuid(), nullable=True))
    op.add_column(
        'identity_users', sa.Column('remark', sa.String(length=2000), server_default=sa.text("''"), nullable=False)
    )
    op.add_column('identity_users', sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.add_column('identity_users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('identity_users', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('identity_users', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_identity_users_department_id'), 'identity_users', ['department_id'], unique=False)
    op.create_foreign_key(
        'fk_identity_users_department_id', 'identity_users', 'identity_departments', ['department_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_identity_users_department_id', 'identity_users', type_='foreignkey')
    op.drop_index(op.f('ix_identity_users_department_id'), table_name='identity_users')
    op.drop_column('identity_users', 'updated_at')
    op.drop_column('identity_users', 'created_at')
    op.drop_column('identity_users', 'deleted_at')
    op.drop_column('identity_users', 'version')
    op.drop_column('identity_users', 'remark')
    op.drop_column('identity_users', 'department_id')
    op.drop_column('identity_users', 'phone')
    op.drop_column('identity_users', 'email')
    op.drop_column('identity_users', 'display_name')
    op.drop_constraint('uq_identity_sessions_id', 'identity_sessions', type_='unique')
    op.drop_column('identity_sessions', 'created_at')
    op.drop_column('identity_sessions', 'version')
    op.drop_column('identity_sessions', 'id')
    op.drop_table('identity_user_roles')
    op.drop_table('identity_user_posts')
    op.drop_index(op.f('ix_operations_dictionary_items_dictionary_id'), table_name='operations_dictionary_items')
    op.drop_table('operations_dictionary_items')
    op.drop_index(op.f('ix_operations_notices_status'), table_name='operations_notices')
    op.drop_table('operations_notices')
    op.drop_index(op.f('ix_operations_logs_username'), table_name='operations_logs')
    op.drop_index(op.f('ix_operations_logs_outcome'), table_name='operations_logs')
    op.drop_index(op.f('ix_operations_logs_kind'), table_name='operations_logs')
    op.drop_index(op.f('ix_operations_logs_created_at'), table_name='operations_logs')
    op.drop_table('operations_logs')
    op.drop_table('operations_dictionaries')
    op.drop_table('operations_configs')
    op.drop_table('identity_roles')
    op.drop_table('identity_posts')
    op.drop_index(op.f('ix_identity_menus_parent_id'), table_name='identity_menus')
    op.drop_table('identity_menus')
    op.drop_index(op.f('ix_identity_departments_parent_id'), table_name='identity_departments')
    op.drop_table('identity_departments')
