"""Personal skills retain legacy builtin archives and immutable publication facts."""

import json
import logging
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0014_personal_skill_ownership'
down_revision = '0013_skill_revision_history'
branch_labels = None
depends_on = None


def _preflight(connection: sa.Connection) -> list[dict[str, object]]:
    # Only an unambiguous, confirmed installation establishes ownership. Never
    # infer ownership from an administrator role, latest update or a reference.
    claims = (
        connection.execute(
            sa.text("""SELECT s.id, s.slug,
        array_agg(DISTINCT d.owner_id) FILTER (WHERE d.owner_id IS NOT NULL) AS owners,
        array_agg(DISTINCT d.id) FILTER (WHERE d.id IS NOT NULL) AS draft_ids
        FROM skills s LEFT JOIN skill_drafts d ON d.confirmed_skill_id=s.id
          AND d.action='install' AND d.target_skill_id IS NULL
        WHERE s.source='upload' GROUP BY s.id,s.slug ORDER BY s.id""")
        )
        .mappings()
        .all()
    )
    checks = (
        (
            'agents',
            """SELECT a.id,a.owner_id FROM agents a WHERE a.deleted_at IS NULL
            AND a.config->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text)) AND (CAST(:owner AS uuid) IS NULL OR a.owner_id<>:owner)""",
        ),
        (
            'agent_publications',
            """SELECT a.id,a.owner_id FROM agents a JOIN agent_versions v
            ON v.agent_id=a.id AND v.version=a.published_version WHERE a.deleted_at IS NULL
            AND v.config->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text)) AND (CAST(:owner AS uuid) IS NULL OR a.owner_id<>:owner)""",
        ),
        (
            'work_members',
            """SELECT m.id,m.agent_owner_id AS owner_id FROM work_members m
            JOIN work_tasks t ON t.id=m.task_id WHERE t.deleted_at IS NULL AND t.status='active'
            AND m.removed_at IS NULL AND m.config->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text))
            AND (CAST(:owner AS uuid) IS NULL OR m.agent_owner_id<>:owner)""",
        ),
        (
            'agent_runs',
            """SELECT r.id,a.owner_id FROM runtime_runs r JOIN agent_run_audits a ON a.run_id=r.id
            WHERE r.status IN ('pending','running','cancel_requested')
            AND a.config->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text)) AND (CAST(:owner AS uuid) IS NULL OR a.owner_id<>:owner)""",
        ),
        (
            'chat_runs',
            """SELECT r.id,c.owner_id FROM runtime_runs r JOIN chat_messages m ON m.run_id=r.id
            JOIN chat_conversations c ON c.id=m.conversation_id
            WHERE r.status IN ('pending','running','cancel_requested')
            AND m.skill_snapshots @> jsonb_build_array(jsonb_build_object('slug',CAST(:slug AS text))) AND (CAST(:owner AS uuid) IS NULL OR c.owner_id<>:owner)""",
        ),
        (
            'work_runs',
            """SELECT r.id,(m.audit->>'agent_owner_id')::uuid AS owner_id FROM runtime_runs r JOIN work_messages m ON m.run_id=r.id
            JOIN work_tasks t ON t.id=m.task_id WHERE r.status IN ('pending','running','cancel_requested')
            AND m.audit->'config'->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text))
            AND (CAST(:owner AS uuid) IS NULL OR (m.audit->>'agent_owner_id') IS NULL OR (m.audit->>'agent_owner_id')::uuid<>:owner)""",
        ),
        (
            'preparations',
            """WITH refs AS (SELECT p.id,
                CASE WHEN p.resource_type='work_turn' THEN
                        (SELECT m.agent_owner_id FROM work_members m WHERE m.task_id=p.resource_id
                         AND m.agent_id=(p.request->'body'->>'agent_id')::uuid AND m.removed_at IS NULL)
                    WHEN p.resource_type IN ('agent_preview','agent_invocation') THEN
                        (SELECT a.owner_id FROM agents a WHERE a.id=p.resource_id)
                    ELSE p.owner_id END AS owner_id
                FROM knowledge_retrieval_intents p
                WHERE p.status NOT IN ('admitted','completed','failed','cancelled','interrupted') AND (
                    p.dependencies->'config'->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text)) OR
                    p.dependencies->'member_config'->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text)) OR
                    p.dependencies->'agent_config'->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text)) OR
                    p.request->'skill_slugs' @> jsonb_build_array(CAST(:slug AS text))))
                SELECT id,owner_id FROM refs WHERE CAST(:owner AS uuid) IS NULL OR owner_id IS NULL OR owner_id<>:owner""",
        ),
    )
    report = []
    for claim in claims:
        candidates = sorted(claim['owners'] or [], key=str)
        owner = candidates[0] if len(candidates) == 1 else None
        conflicts = []
        for label, query in checks:
            rows = connection.execute(sa.text(query), {'slug': claim['slug'], 'owner': owner}).all()
            conflicts.extend({'kind': label, 'id': row[0], 'owner_id': row[1]} for row in rows)
        report.append(
            {
                'skill_id': claim['id'],
                'slug': claim['slug'],
                'owner_id': owner,
                'decision': 'assigned' if owner is not None else 'ambiguous' if candidates else 'missing',
                'candidate_owner_ids': candidates,
                'install_draft_ids': sorted(claim['draft_ids'] or [], key=str),
                'blocked_references': conflicts,
            }
        )
    # Emit before DDL so an aborted upgrade still leaves a concrete audit list.
    logging.getLogger(__name__).warning(
        'Personal skill ownership audit: %s', json.dumps(report, default=str, sort_keys=True)
    )
    if any(row['blocked_references'] for row in report):
        raise RuntimeError(
            'Personal skill migration audit required: unresolved active references: '
            + json.dumps(report, default=str, sort_keys=True)
        )
    return report


def upgrade() -> None:
    connection = op.get_bind()
    report = _preflight(connection)
    op.add_column('skills', sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id')))
    op.add_column('skills', sa.Column('legacy_archive', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('skills', sa.Column('ownership_audit', postgresql.JSONB()))
    op.drop_constraint('skills_slug_key', 'skills', type_='unique')
    op.create_unique_constraint('uq_skills_owner_slug', 'skills', ['owner_id', 'slug'])
    op.create_index('ix_skills_owner_id', 'skills', ['owner_id'])
    bindings = op.create_table(
        'skill_legacy_bindings',
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), primary_key=True),
        sa.Column('legacy_skill_id', sa.Uuid(), sa.ForeignKey('skills.id'), primary_key=True),
        sa.Column('personal_skill_id', sa.Uuid(), sa.ForeignKey('skills.id'), nullable=False),
    )
    op.create_index('ix_skill_legacy_bindings_personal_skill_id', 'skill_legacy_bindings', ['personal_skill_id'])
    connection.execute(
        sa.text("""UPDATE skills SET legacy_archive=true,
        ownership_audit='{"decision":"builtin_template"}'::jsonb WHERE source='builtin'""")
    )
    for row in report:
        connection.execute(
            sa.text("""UPDATE skills SET owner_id=:owner, legacy_archive=:archive,
            ownership_audit=CAST(:audit AS jsonb) WHERE id=:id"""),
            {
                'owner': row['owner_id'],
                'archive': row['owner_id'] is None,
                'id': row['skill_id'],
                'audit': json.dumps(row, default=str, sort_keys=True),
            },
        )
    op.create_check_constraint(
        'ck_skills_archive_owner',
        'skills',
        '(owner_id IS NOT NULL AND NOT legacy_archive) OR (owner_id IS NULL AND legacy_archive)',
    )

    templates = (
        connection.execute(sa.text("SELECT * FROM skills WHERE source='builtin' AND legacy_archive ORDER BY slug"))
        .mappings()
        .all()
    )
    owners = connection.scalars(sa.text('SELECT id FROM identity_users ORDER BY id')).all()
    for owner_id in owners:
        for template in templates:
            skill_id, revision_id = uuid4(), uuid4()
            connection.execute(
                sa.text("""INSERT INTO skills
                (id,owner_id,slug,status,source,version,revision_id,created_at,updated_at,deleted_at)
                VALUES (:id,:owner,:slug,:status,'builtin',:version,:revision,:created,:updated,:deleted)"""),
                {
                    'id': skill_id,
                    'owner': owner_id,
                    'slug': template['slug'],
                    'status': template['status'],
                    'version': template['version'],
                    'revision': revision_id,
                    'created': template['created_at'],
                    'updated': template['updated_at'],
                    'deleted': template['deleted_at'],
                },
            )
            connection.execute(
                sa.text("""INSERT INTO skill_revisions
                (id,skill_id,name,description,package_version,content,content_hash,package_sha,
                 package_bytes,filename,file_count,total_bytes)
                SELECT :revision,:skill,name,description,package_version,content,content_hash,package_sha,
                 package_bytes,filename,file_count,total_bytes FROM skill_revisions WHERE id=:legacy"""),
                {'revision': revision_id, 'skill': skill_id, 'legacy': template['revision_id']},
            )
            op.bulk_insert(
                bindings, [{'owner_id': owner_id, 'legacy_skill_id': template['id'], 'personal_skill_id': skill_id}]
            )


def downgrade() -> None:
    raise RuntimeError('Cannot downgrade personal skill ownership without losing owner boundaries and legacy bindings')
