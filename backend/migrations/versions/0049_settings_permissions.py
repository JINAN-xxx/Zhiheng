"""Split notice and knowledge settings grants and remove the unused parsing grant."""

import json

import sqlalchemy as sa
from alembic import op

revision = '0049_settings_permissions'
down_revision = '0048_admin_skill_library'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE identity_roles IN SHARE ROW EXCLUSIVE MODE')
    rows = connection.execute(sa.text('SELECT id, permissions FROM identity_roles')).mappings()
    for row in rows:
        codes = set(row['permissions'] or [])
        legacy = {code for code in codes if code.startswith('system:configs:')}
        if not legacy and 'ai:storage:parse' not in codes:
            continue
        updated = codes - legacy - {'ai:storage:parse'}
        for code in legacy:
            action = code.rsplit(':', 1)[1]
            if action in {'enabled', 'read', 'create', 'update', 'delete', 'toggle'}:
                updated.add(f'system:notice_settings:{action}')
            if action in {'enabled', 'read', 'update'}:
                updated.add(f'platform:knowledge_settings:{action}')
        connection.execute(
            sa.text(
                'UPDATE identity_roles SET permissions=CAST(:permissions AS json), '
                'version=version+1, updated_at=now() WHERE id=:id'
            ),
            {'id': row['id'], 'permissions': json.dumps(sorted(updated))},
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE identity_roles IN SHARE ROW EXCLUSIVE MODE')
    rows = connection.execute(sa.text('SELECT permissions FROM identity_roles')).scalars()
    if any(
        code.startswith(('system:notice_settings:', 'platform:knowledge_settings:'))
        for codes in rows
        for code in (codes or [])
    ):
        raise RuntimeError('Restore a reviewed role-permission backup before merging settings grants.')
