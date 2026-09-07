import re
from pathlib import Path

from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from plugins.ai.entity.do.ai_chat_platform_do import (
    AiChatMessage,
    AiChatPreferences,
    AiChatRun,
    AiChatSession,
)
from plugins.core.discovery.scanner import PluginScanner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'ai'
CHAT_TABLES = {
    'ai_chat_session',
    'ai_chat_message',
    'ai_chat_run',
    'ai_chat_preferences',
}
RESOURCE_ID_LENGTH = 36
ERROR_MESSAGE_MAX_LENGTH = 500


def _read_migration(database: str) -> str:
    return (PLUGIN_ROOT / 'migrations' / database / '003_platform_chat.sql').read_text(encoding='utf-8')


def _read_skill_migration(database: str) -> str:
    return (PLUGIN_ROOT / 'migrations' / database / '008_chat_skill_snapshots.sql').read_text(encoding='utf-8')


def _normalize_sql(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql.lower()).strip()


def _check_constraints(table: object) -> dict[str, str]:
    return {
        constraint.name: _normalize_sql(str(constraint.sqltext))
        for constraint in table.constraints
        if getattr(constraint, 'name', None) and hasattr(constraint, 'sqltext')
    }


def _assert_foreign_key(column: object, target: str) -> None:
    assert len(column.foreign_keys) == 1
    foreign_key = next(iter(column.foreign_keys))
    assert str(foreign_key.target_fullname) == target
    assert foreign_key.ondelete == 'RESTRICT'


def test_platform_chat_migrations_are_registered_after_model_foundation() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    assert plugin.manifest.version == '0.8.0'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_init.sql',
        'migrations/mysql/002_model_service_foundation.sql',
        'migrations/mysql/003_platform_chat.sql',
        'migrations/mysql/004_developer_api_foundation.sql',
        'migrations/mysql/005_embedding_rerank_gateway.sql',
        'migrations/mysql/006_quota_reservation.sql',
        'migrations/mysql/007_application_lifecycle.sql',
        'migrations/mysql/008_chat_skill_snapshots.sql',
        'migrations/postgresql/001_init.sql',
        'migrations/postgresql/002_model_service_foundation.sql',
        'migrations/postgresql/003_platform_chat.sql',
        'migrations/postgresql/004_developer_api_foundation.sql',
        'migrations/postgresql/005_embedding_rerank_gateway.sql',
        'migrations/postgresql/006_quota_reservation.sql',
        'migrations/postgresql/007_application_lifecycle.sql',
        'migrations/postgresql/008_chat_skill_snapshots.sql',
    ]


def test_platform_chat_skill_snapshot_migrations_are_additive() -> None:
    for database in ('mysql', 'postgresql'):
        sql = _normalize_sql(_read_skill_migration(database))
        assert 'alter table ai_chat_message' in sql
        assert 'skill_snapshots' in sql
        assert 'drop table' not in sql


def test_platform_chat_message_has_skill_snapshot_json_column() -> None:
    assert AiChatMessage.__table__.c.skill_snapshots.nullable is False
    assert callable(AiChatMessage.__table__.c.skill_snapshots.default.arg)
    assert AiChatMessage.__table__.c.skill_snapshots.default.arg(None) == []


def test_platform_chat_migrations_are_additive_and_leave_legacy_tables_alone() -> None:
    for database in ('mysql', 'postgresql'):
        sql = _normalize_sql(_read_migration(database))

        assert 'drop table' not in sql
        assert 'alter table ai_models' not in sql
        assert 'alter table ai_chat_config' not in sql
        assert 'create table ai_sessions' not in sql
        for table_name in CHAT_TABLES:
            assert f'create table if not exists {table_name}' in sql
        assert sql.count('create table if not exists') == len(CHAT_TABLES)


def test_platform_chat_migrations_define_ids_foreign_keys_and_unique_message_key() -> None:
    for database in ('mysql', 'postgresql'):
        sql = _normalize_sql(_read_migration(database))

        for id_name in ('session_id', 'message_id', 'run_id'):
            assert f'{id_name} varchar(36) not null' in sql
        assert 'user_id bigint not null' in sql
        assert 'unique (session_id, client_message_id)' in sql
        assert 'foreign key (model_id) references ai_model_catalog (model_id) on delete restrict' in sql
        assert 'foreign key (binding_id) references ai_resource_binding (binding_id) on delete restrict' in sql
        assert 'foreign key (session_id) references ai_chat_session (session_id) on delete restrict' in sql


def test_platform_chat_migrations_define_status_and_json_contracts() -> None:
    mysql_sql = _normalize_sql(_read_migration('mysql'))
    postgres_sql = _normalize_sql(_read_migration('postgresql'))

    for sql in (mysql_sql, postgres_sql):
        assert "check (status in ('active', 'archived'))" in sql
        assert "check (role in ('user', 'assistant'))" in sql
        assert "check (status in ('complete', 'interrupted', 'cancelled'))" in sql
        assert "check (status in ('pending', 'running', 'completed', 'cancelled', 'interrupted', 'failed'))" in sql
        assert 'error_message varchar(500)' in sql
    assert 'images json not null' in mysql_sql
    assert 'images jsonb not null' in postgres_sql


def test_platform_chat_entities_match_primary_key_and_foreign_key_contracts() -> None:
    assert {
        table.name
        for table in (
            AiChatSession.__table__,
            AiChatMessage.__table__,
            AiChatRun.__table__,
            AiChatPreferences.__table__,
        )
    } == CHAT_TABLES

    for table, key in (
        (AiChatSession.__table__, 'session_id'),
        (AiChatMessage.__table__, 'message_id'),
        (AiChatRun.__table__, 'run_id'),
    ):
        assert table.c[key].primary_key is True
        assert table.c[key].nullable is False
        assert table.c[key].type.length == RESOURCE_ID_LENGTH

    assert AiChatSession.__table__.c.user_id.nullable is False
    _assert_foreign_key(AiChatSession.__table__.c.model_id, 'ai_model_catalog.model_id')
    _assert_foreign_key(AiChatMessage.__table__.c.session_id, 'ai_chat_session.session_id')
    _assert_foreign_key(AiChatRun.__table__.c.session_id, 'ai_chat_session.session_id')
    _assert_foreign_key(AiChatRun.__table__.c.model_id, 'ai_model_catalog.model_id')
    _assert_foreign_key(AiChatRun.__table__.c.binding_id, 'ai_resource_binding.binding_id')


def test_platform_chat_entities_define_unique_and_status_constraints() -> None:
    message_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AiChatMessage.__table__.constraints
        if constraint.__class__.__name__ == 'UniqueConstraint'
    }
    assert ('session_id', 'client_message_id') in message_unique_columns

    assert "status in ('active', 'archived')" in _check_constraints(AiChatSession.__table__).values()
    message_constraints = _check_constraints(AiChatMessage.__table__).values()
    assert "role in ('user', 'assistant')" in message_constraints
    assert "status in ('complete', 'interrupted', 'cancelled')" in message_constraints
    assert (
        "status in ('pending', 'running', 'completed', 'cancelled', 'interrupted', 'failed')"
        in _check_constraints(AiChatRun.__table__).values()
    )


def test_platform_chat_entities_use_fresh_json_and_time_defaults() -> None:
    images_default = AiChatMessage.__table__.c.images.default.arg
    assert callable(images_default)
    assert images_default(None) == []
    assert images_default(None) is not images_default(None)

    for table in (AiChatSession.__table__, AiChatMessage.__table__, AiChatRun.__table__, AiChatPreferences.__table__):
        assert callable(table.c.created_at.default.arg)
        assert callable(table.c.updated_at.default.arg)
        assert callable(table.c.updated_at.onupdate.arg)


def test_platform_chat_preferences_defaults_and_identity_compile_for_both_databases() -> None:
    table = AiChatPreferences.__table__
    assert table.c.preference_id.primary_key is True
    assert table.c.preference_id.autoincrement is True
    assert table.c.user_id.unique is True
    assert table.c.add_history.server_default is not None
    assert table.c.history_message_count.server_default is not None
    assert table.c.metrics_visible.server_default is not None
    assert table.c.vision_enabled.server_default is not None
    assert table.c.image_max_size_mb.server_default is not None

    mysql_ddl = _normalize_sql(str(CreateTable(table).compile(dialect=mysql.dialect())))
    postgres_ddl = _normalize_sql(str(CreateTable(table).compile(dialect=postgresql.dialect())))
    assert 'auto_increment' in mysql_ddl
    assert 'generated by default as identity' in postgres_ddl


def test_platform_chat_run_uses_scalar_usage_and_auditable_snapshots() -> None:
    table = AiChatRun.__table__
    required_columns = {
        'engine_type',
        'engine_model_snapshot',
        'engine_request_id',
        'trace_id',
        'input_tokens',
        'output_tokens',
        'reasoning_tokens',
        'cache_read_tokens',
        'cache_write_tokens',
        'raw_cost',
        'error_code',
        'error_message',
        'started_at',
        'finished_at',
    }
    assert required_columns <= set(table.c.keys())
    assert table.c.engine_type.nullable is False
    assert table.c.engine_model_snapshot.nullable is False
    assert table.c.error_message.type.length == ERROR_MESSAGE_MAX_LENGTH
