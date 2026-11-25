"""Move default skills to the admin's private library and retire account copies."""

from alembic import op

revision = '0048_admin_skill_library'
down_revision = '0047_platform_skills'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('LOCK TABLE identity_users IN SHARE ROW EXCLUSIVE MODE')
    op.execute(
        """UPDATE skills AS skill
        SET source = 'upload',
            deleted_at = CASE
                WHEN owner.username = 'admin' AND owner.is_admin AND owner.is_active AND owner.deleted_at IS NULL
                THEN skill.deleted_at
                ELSE coalesce(skill.deleted_at, now())
            END,
            version = skill.version + 1,
            updated_at = now()
        FROM identity_users AS owner
        WHERE skill.owner_id = owner.id AND skill.source = 'builtin'"""
    )


def downgrade() -> None:
    # Source and retirement are data decisions. Keep them and any later edits on schema rollback.
    pass
