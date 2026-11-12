"""Persist the protected system role and existing administrators' memberships."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = '0011_builtin_admin_role'
down_revision = '0010_admin_console'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('identity_roles', sa.Column('system_key', sa.String(80), nullable=True))
    op.create_unique_constraint('uq_identity_roles_system_key', 'identity_roles', ['system_key'])
    roles = sa.table(
        'identity_roles',
        sa.column('id', sa.Uuid()),
        sa.column('code', sa.String()),
        sa.column('name', sa.String()),
        sa.column('permissions', sa.JSON()),
        sa.column('is_active', sa.Boolean()),
        sa.column('remark', sa.String()),
        sa.column('version', sa.Integer()),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('updated_at', sa.DateTime(timezone=True)),
        sa.column('system_key', sa.String()),
    )
    connection = op.get_bind()
    codes = set(connection.scalars(sa.select(roles.c.code)))
    code = 'super_admin'
    index = 1
    while code in codes:
        code = 'system_super_admin' if index == 1 else f'system_super_admin_{index}'
        index += 1
    role_id = uuid4()
    connection.execute(
        roles.insert().values(
            id=role_id,
            code=code,
            name='超级管理员',
            permissions=[],
            is_active=True,
            remark='系统内置角色，超级管理员身份由系统维护',
            version=1,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
            system_key='super_admin',
        )
    )
    users = sa.table('identity_users', sa.column('id', sa.Uuid()), sa.column('is_admin', sa.Boolean()))
    memberships = sa.table(
        'identity_user_roles',
        sa.column('user_id', sa.Uuid()),
        sa.column('role_id', sa.Uuid()),
    )
    connection.execute(
        memberships.insert().from_select(
            ['user_id', 'role_id'],
            sa.select(users.c.id, sa.literal(role_id, type_=sa.Uuid())).where(users.c.is_admin.is_(True)),
        )
    )


def downgrade() -> None:
    op.execute(
        'DELETE FROM identity_user_roles WHERE role_id IN '
        "(SELECT id FROM identity_roles WHERE system_key='super_admin')"
    )
    op.execute("DELETE FROM identity_roles WHERE system_key='super_admin'")
    op.drop_constraint('uq_identity_roles_system_key', 'identity_roles', type_='unique')
    op.drop_column('identity_roles', 'system_key')
