"""Immutable skill packages and frozen chat selection. Builtins derive from MIT legacy sources."""

import hashlib
from datetime import datetime, timezone
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0003_skills'
down_revision = '0002_developer'
branch_labels = None
depends_on = None


BUILTINS = (
    (
        'deep-research',
        '---\nname: deep-research\ndisplay_name: 深度研究\ndescription: 为复杂主题制定研究计划，核对事实并形成结构化报告。\nversion: 1.0.0\n---\n\n# 深度研究\n\n先明确研究问题、范围和成功标准，再拆分为可验证的子问题。\n\n回答时区分事实、推断与未知信息，优先提供可核查的依据。最终输出应包含结论、关键证据、局限和下一步建议。\n\n',
    ),
    (
        'document-summary',
        '---\nname: document-summary\ndisplay_name: 文档总结\ndescription: 提取长文档中的主题、结论、风险和待办事项。\nversion: 1.0.0\n---\n\n# 文档总结\n\n先识别文档目标和结构，再提取核心事实、重要结论、争议点、风险与行动项。\n\n不要编造文档中不存在的信息。信息不足时明确指出缺失内容，并保持术语和数值准确。\n\n',
    ),
)


def upgrade() -> None:
    skills = op.create_table(
        'skills',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('slug', sa.String(255), nullable=False, unique=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('revision_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('enabled','disabled')"),
        sa.CheckConstraint("source IN ('builtin','upload')"),
    )
    revisions = op.create_table(
        'skill_revisions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('skill_id', sa.Uuid(), sa.ForeignKey('skills.id'), nullable=False, unique=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.String(500), nullable=False),
        sa.Column('package_version', sa.String(64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('package_sha', sa.String(64), nullable=False),
        sa.Column('package_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('file_count', sa.Integer(), nullable=False),
        sa.Column('total_bytes', sa.Integer(), nullable=False),
    )
    op.create_table(
        'skill_drafts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('package_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('package_sha', sa.String(64), nullable=False),
        sa.Column('parsed_hash', sa.String(64), nullable=False),
        sa.Column('confirmed_skill_id', sa.Uuid(), sa.ForeignKey('skills.id')),
    )
    op.create_index('ix_skill_drafts_expires_at', 'skill_drafts', ['expires_at'])
    op.add_column(
        'chat_messages', sa.Column('skill_snapshots', postgresql.JSONB(), server_default='[]', nullable=False)
    )
    op.execute("""CREATE FUNCTION reject_skill_revision_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'skill revisions are immutable' USING ERRCODE = '55000'; END;
        $$""")
    op.execute("""CREATE TRIGGER immutable_skill_revisions BEFORE UPDATE OR DELETE ON skill_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_skill_revision_mutation()""")
    # Frozen literals make upgrades independent of deployed resources and parser changes.
    for index, (slug, content) in enumerate(BUILTINS, 1):
        skill_id = UUID(f'00000000-0000-4000-8000-{index:012d}')
        revision_id = UUID(f'00000000-0000-4000-9000-{index:012d}')
        raw = content.encode('utf-8')
        # These legacy front matter values are frozen alongside their full instruction text.
        header = dict(line.split(': ', 1) for line in content.split('---', 2)[1].strip().splitlines())
        path = b'SKILL.md'
        tree = hashlib.sha256(len(path).to_bytes(4, 'big') + path + len(raw).to_bytes(8, 'big') + raw).hexdigest()
        now = datetime.now(timezone.utc)
        op.bulk_insert(
            skills,
            [
                dict(
                    id=skill_id,
                    slug=slug,
                    status='enabled',
                    source='builtin',
                    version=1,
                    revision_id=revision_id,
                    created_at=now,
                    updated_at=now,
                )
            ],
        )
        op.bulk_insert(
            revisions,
            [
                dict(
                    id=revision_id,
                    skill_id=skill_id,
                    name=header['display_name'],
                    description=header['description'],
                    package_version=header.get('version', '1.0.0'),
                    content=content,
                    content_hash=tree,
                    package_sha=hashlib.sha256(raw).hexdigest(),
                    package_bytes=raw,
                    filename='SKILL.md',
                    file_count=1,
                    total_bytes=len(raw),
                )
            ],
        )


def downgrade() -> None:
    op.drop_column('chat_messages', 'skill_snapshots')
    op.drop_table('skill_drafts')
    op.drop_table('skill_revisions')
    op.execute('DROP FUNCTION reject_skill_revision_mutation()')
    op.drop_table('skills')
