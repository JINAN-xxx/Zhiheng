"""Split MCP administration grants and distinguish personal history and feedback writes."""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = '0051_operation_permissions'
down_revision = '0050_mcp_connections'
branch_labels = None
depends_on = None

MCP_LEGACY = 'platform:mcp:manage'
MCP_OPERATIONS = frozenset({'platform:mcp:configure', 'platform:mcp:grant', 'platform:mcp:audit'})
PERSONAL_OPERATIONS = {
    'platform:agents:use': frozenset({'platform:agents:conversation_update', 'platform:agents:conversation_delete'}),
    'platform:chat:read': frozenset({'platform:chat:feedback'}),
}


def _write(connection: Connection, role_id: object, codes: set[str]) -> None:
    connection.execute(
        sa.text(
            'UPDATE identity_roles SET permissions=CAST(:permissions AS json), '
            'version=version+1, updated_at=now() WHERE id=:id'
        ),
        {'id': role_id, 'permissions': json.dumps(sorted(codes))},
    )


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE identity_roles IN SHARE ROW EXCLUSIVE MODE')
    rows = connection.execute(sa.text('SELECT id, permissions FROM identity_roles')).mappings()
    for row in rows:
        codes = set(row['permissions'] or [])
        updated = codes.copy()
        if MCP_LEGACY in codes:
            updated.remove(MCP_LEGACY)
            updated.update(MCP_OPERATIONS)
        for previous, operations in PERSONAL_OPERATIONS.items():
            if previous in codes:
                updated.update(operations)
        if updated != codes:
            _write(connection, row['id'], updated)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE identity_roles IN SHARE ROW EXCLUSIVE MODE')
    rows = connection.execute(sa.text('SELECT id, permissions FROM identity_roles')).mappings()
    for row in rows:
        codes = set(row['permissions'] or [])
        updated = codes.copy()
        mcp = codes & MCP_OPERATIONS
        if mcp:
            if mcp != MCP_OPERATIONS:
                raise RuntimeError('Restore a reviewed role backup before merging partial MCP grants.')
            updated.difference_update(MCP_OPERATIONS)
            updated.add(MCP_LEGACY)
        for previous, operations in PERSONAL_OPERATIONS.items():
            implied = operations if previous in codes else frozenset()
            if codes & operations != implied:
                raise RuntimeError('Restore a reviewed role backup before merging personal operation grants.')
            updated.difference_update(operations)
        if updated != codes:
            _write(connection, row['id'], updated)
