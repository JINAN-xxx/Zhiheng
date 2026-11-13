"""Restore market presets without enabling previously deleted personal copies."""

import sqlalchemy as sa
from alembic import op

revision = '0032_skill_market'
down_revision = '0031_assistant_turn_versions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        UPDATE skills
        SET ownership_audit = COALESCE(ownership_audit, '{}'::jsonb)
            || jsonb_build_object('market_restore_0032', jsonb_build_object(
                'deleted_at', deleted_at, 'status', status, 'version', version,
                'updated_at', updated_at, 'ownership_audit', ownership_audit)),
            deleted_at = NULL, status = 'disabled', version = version + 1, updated_at = now()
        WHERE owner_id IS NOT NULL AND source = 'builtin' AND deleted_at IS NOT NULL
    """)
    )


def downgrade() -> None:
    connection = op.get_bind()
    changed = connection.scalar(
        sa.text("""
        SELECT count(*) FROM skills WHERE ownership_audit ? 'market_restore_0032'
        AND (version != (ownership_audit->'market_restore_0032'->>'version')::integer + 1
             OR status != 'disabled' OR deleted_at IS NOT NULL)
    """)
    )
    if changed:
        raise RuntimeError('Restored market skills were modified; restore their state before downgrading')
    op.execute(
        sa.text("""
        UPDATE skills SET
            deleted_at = (ownership_audit->'market_restore_0032'->>'deleted_at')::timestamptz,
            status = ownership_audit->'market_restore_0032'->>'status',
            version = (ownership_audit->'market_restore_0032'->>'version')::integer,
            updated_at = (ownership_audit->'market_restore_0032'->>'updated_at')::timestamptz,
            ownership_audit = NULLIF(ownership_audit->'market_restore_0032'->'ownership_audit', 'null'::jsonb)
        WHERE ownership_audit ? 'market_restore_0032'
    """)
    )
