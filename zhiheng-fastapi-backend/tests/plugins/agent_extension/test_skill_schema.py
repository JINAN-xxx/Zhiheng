import re
from pathlib import Path

from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from plugins.agent_extension.entity.do.skill_do import AgentSkill

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'agent_extension'


def _normalize(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql.lower()).strip()


def test_skill_migrations_define_matching_safe_schema() -> None:
    mysql_sql = _normalize((PLUGIN_ROOT / 'migrations/mysql/001_skill_management.sql').read_text(encoding='utf-8'))
    postgres_sql = _normalize(
        (PLUGIN_ROOT / 'migrations/postgresql/001_skill_management.sql').read_text(encoding='utf-8')
    )

    for sql in (mysql_sql, postgres_sql):
        assert 'create table if not exists agent_skill' in sql
        assert 'skill_id varchar(36) not null' in sql
        assert 'skill_slug varchar(100) not null' in sql
        assert 'unique (skill_slug)' in sql
        assert "check (status in ('enabled', 'disabled'))" in sql
        assert "check (source_type in ('builtin', 'upload'))" in sql
        assert 'lock_version' in sql
        assert 'content_hash varchar(64) not null' in sql
        assert 'storage_path varchar(512) not null' in sql
        assert 'drop table' not in sql
    assert 'metadata_json json not null' in mysql_sql
    assert 'metadata_json jsonb not null' in postgres_sql


def test_skill_entity_matches_schema_for_both_databases() -> None:
    assert AgentSkill.__tablename__ == 'agent_skill'
    assert AgentSkill.__table__.c.skill_id.primary_key is True
    assert AgentSkill.__table__.c.skill_slug.unique is True
    assert AgentSkill.__table__.c.status.server_default is not None
    assert AgentSkill.__table__.c.lock_version.server_default is not None
    assert AgentSkill.__table__.c.metadata_json.nullable is False

    mysql_ddl = _normalize(str(CreateTable(AgentSkill.__table__).compile(dialect=mysql.dialect())))
    postgres_ddl = _normalize(str(CreateTable(AgentSkill.__table__).compile(dialect=postgresql.dialect())))
    assert 'metadata_json json not null' in mysql_ddl
    assert 'metadata_json jsonb not null' in postgres_ddl
    assert "status in ('enabled', 'disabled')" in mysql_ddl
    assert "status in ('enabled', 'disabled')" in postgres_ddl
