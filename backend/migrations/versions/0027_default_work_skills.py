"""Add three personal skill templates and backfill missing copies."""

import hashlib
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = '0027_default_work_skills'
down_revision = '0026_model_output_range'
branch_labels = None
depends_on = None

# Freeze the package text with the migration, as for the original builtin templates.
TEMPLATES = (
    (
        'meeting-minutes',
        '会议纪要',
        '从会议记录或转写稿中提取已确认的结论、行动项和待确认问题。',
        """根据用户提供的记录整理会议纪要。把发言、提议和已确认的决定分开，合并重复内容。

依次输出：
1. 会议结论：列出已达成的一致意见。若没有已确认结论，明确说明。
2. 行动项：用表格列出事项、负责人、截止时间。缺失或无法确认的字段标为「待确认」。
3. 待确认问题：列出有分歧、未定或缺少信息的事项。

保留人名、日期、数字和原意；不要把讨论中的建议写成决定，也不要自行分配任务。
""",
    ),
    (
        'writing-polish',
        '文字润色',
        '按读者和使用场景调整措辞、结构与语气，产出可直接使用的正文。',
        """润色用户提供的草稿。优先遵循用户指定的受众、用途、语气和篇幅；未指定时使用自然、清楚的措辞，沿用原文语言。

整理表达顺序，消除重复和歧义。保留事实、人名、数字、时间和承诺的边界，不添加原文没有的事实、条件或承诺。遇到含义不明且会改变结论的内容，保留原意并在正文后简短标明待确认处。

默认只输出一版可以直接使用的正文。用户明确要求对照或说明时，再补充修改说明；若未提供草稿，请先请用户提供。
""",
    ),
    (
        'requirements-clarification',
        '需求梳理',
        '把想法和零散反馈整理成目标、范围、验收标准与待确认问题。',
        """将用户的想法、反馈和材料整理为可讨论、可验收的需求。把已明确的要求和建议项分开。

依次输出：
1. 目标：目标用户、触发场景和要解决的问题。
2. 范围：明确本次要做的功能、边界及已确认排除的事项。
3. 验收标准：按「在什么条件下、用户做什么、看到什么结果」写出可核对的条目，覆盖已知异常情况。
4. 待确认问题：列出会影响范围或验收的关键未知项。

依据当前材料归纳。没有依据时标为「待确认」，不要替用户决定范围、交付时间或实现方案。
""",
    ),
)


def upgrade() -> None:
    connection = op.get_bind()
    # Serialize with account creation, which inserts its user before copying templates.
    connection.exec_driver_sql('LOCK TABLE identity_users IN SHARE ROW EXCLUSIVE MODE')
    for index, (slug, name, description, body) in enumerate(TEMPLATES, start=3):
        template_id = UUID(f'00000000-0000-4000-8000-{index:012d}')
        revision_id = UUID(f'00000000-0000-4000-9000-{index:012d}')
        content = (
            f'---\nname: {slug}\ndisplay_name: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\n'
            f'# {name}\n\n{body}'
        )
        raw = content.encode('utf-8')
        filename = b'SKILL.md'
        content_hash = hashlib.sha256(
            len(filename).to_bytes(4, 'big') + filename + len(raw).to_bytes(8, 'big') + raw
        ).hexdigest()
        connection.execute(
            sa.text("""INSERT INTO skills
            (id,slug,status,source,version,revision_id,created_at,updated_at,legacy_archive,ownership_audit)
            VALUES (:template,:slug,'enabled','builtin',1,:revision,now(),now(),true,
                    '{"decision":"builtin_template"}'::jsonb)
            ON CONFLICT (id) DO NOTHING"""),
            {'template': template_id, 'slug': slug, 'revision': revision_id},
        )
        connection.execute(
            sa.text("""INSERT INTO skill_revisions
            (id,skill_id,name,description,package_version,content,content_hash,package_sha,
             package_bytes,filename,file_count,total_bytes)
            VALUES (:revision,:template,:name,:description,'1.0.0',:content,:content_hash,:package_sha,
                    :raw,'SKILL.md',1,:total_bytes)
            ON CONFLICT (id) DO NOTHING"""),
            {
                'revision': revision_id,
                'template': template_id,
                'name': name,
                'description': description,
                'content': content,
                'content_hash': content_hash,
                'package_sha': hashlib.sha256(raw).hexdigest(),
                'raw': raw,
                'total_bytes': len(raw),
            },
        )
        # A match includes disabled and soft-deleted copies. Leave their state and revision intact.
        connection.execute(
            sa.text("""WITH copies AS (
                INSERT INTO skills (id,owner_id,slug,status,source,version,revision_id,created_at,updated_at)
                SELECT gen_random_uuid(),u.id,:slug,'enabled','builtin',1,gen_random_uuid(),now(),now()
                FROM identity_users u
                ON CONFLICT (owner_id,slug) DO NOTHING
                RETURNING id,owner_id,revision_id
            ), revisions AS (
                INSERT INTO skill_revisions
                (id,skill_id,name,description,package_version,content,content_hash,package_sha,
                 package_bytes,filename,file_count,total_bytes)
                SELECT c.revision_id,c.id,r.name,r.description,r.package_version,r.content,r.content_hash,
                       r.package_sha,r.package_bytes,r.filename,r.file_count,r.total_bytes
                FROM copies c CROSS JOIN skill_revisions r WHERE r.id=:revision
                RETURNING skill_id
            )
            INSERT INTO skill_legacy_bindings (owner_id,legacy_skill_id,personal_skill_id)
            SELECT c.owner_id,:template,c.id FROM copies c JOIN revisions r ON r.skill_id=c.id"""),
            {'slug': slug, 'template': template_id, 'revision': revision_id},
        )


def downgrade() -> None:
    # Earlier code can use these templates. Keep the user copies and any immutable references to them.
    pass
