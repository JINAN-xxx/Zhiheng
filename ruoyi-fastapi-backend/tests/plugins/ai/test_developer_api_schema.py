import importlib
import re
from pathlib import Path

from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from config.database import Base
from plugins.core.discovery.scanner import PluginScanner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'ai'
RESOURCE_ID_LENGTH = 36
COST_PRECISION = 20
COST_SCALE = 10
DEVELOPER_API_TABLES = {
    'ai_application',
    'ai_api_key',
    'ai_api_key_model_grant',
    'ai_api_quota_policy',
    'ai_model_rate_card',
    'ai_api_usage_ledger',
    'ai_api_quota_bucket',
}


def _load_entities() -> dict[str, type]:
    module = importlib.import_module('plugins.ai.entity.do.ai_developer_api_do')
    return {
        'ai_application': module.AiApplication,
        'ai_api_key': module.AiApiKey,
        'ai_api_key_model_grant': module.AiApiKeyModelGrant,
        'ai_api_quota_policy': module.AiApiQuotaPolicy,
        'ai_model_rate_card': module.AiModelRateCard,
        'ai_api_usage_ledger': module.AiApiUsageLedger,
        'ai_api_quota_bucket': module.AiApiQuotaBucket,
    }


def _read_migration(database: str) -> str:
    path = PLUGIN_ROOT / 'migrations' / database / '004_developer_api_foundation.sql'
    return path.read_text(encoding='utf-8')


def _normalize_sql(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql.lower()).strip()


def _constraint_sql(table: object) -> dict[str, str]:
    return {
        constraint.name: _normalize_sql(str(constraint.sqltext))
        for constraint in table.constraints
        if getattr(constraint, 'name', None) and hasattr(constraint, 'sqltext')
    }


def _assert_restrict_foreign_key(column: object, target: str) -> None:
    assert len(column.foreign_keys) == 1
    foreign_key = next(iter(column.foreign_keys))
    assert str(foreign_key.target_fullname) == target
    assert foreign_key.ondelete == 'RESTRICT'


def test_developer_api_migrations_are_registered_after_platform_chat() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    assert plugin.manifest.version == '0.6.0'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_init.sql',
        'migrations/mysql/002_model_service_foundation.sql',
        'migrations/mysql/003_platform_chat.sql',
        'migrations/mysql/004_developer_api_foundation.sql',
        'migrations/mysql/005_embedding_rerank_gateway.sql',
        'migrations/postgresql/001_init.sql',
        'migrations/postgresql/002_model_service_foundation.sql',
        'migrations/postgresql/003_platform_chat.sql',
        'migrations/postgresql/004_developer_api_foundation.sql',
        'migrations/postgresql/005_embedding_rerank_gateway.sql',
    ]


def test_developer_api_migrations_are_additive_and_define_all_tables() -> None:
    for database in ('mysql', 'postgresql'):
        sql = _normalize_sql(_read_migration(database))

        assert 'drop table' not in sql
        assert 'alter table ai_model_catalog' not in sql
        for table_name in DEVELOPER_API_TABLES:
            assert f'create table if not exists {table_name}' in sql
        assert sql.count('create table if not exists') == len(DEVELOPER_API_TABLES)


def test_developer_api_migrations_define_security_and_lifecycle_constraints() -> None:
    for database in ('mysql', 'postgresql'):
        sql = _normalize_sql(_read_migration(database))

        assert 'secret_digest varchar(64) not null' in sql
        assert 'digest_version varchar(32) not null' in sql
        assert 'key_prefix varchar(64) not null' in sql
        assert 'constraint uk_ai_api_key_prefix unique (key_prefix)' in sql
        assert "check (status in ('active', 'retiring', 'revoked', 'expired'))" in sql
        assert "check (status in ('started', 'completed', 'failed', 'cancelled', 'abandoned'))" in sql
        assert 'check (rpm_limit is null or rpm_limit > 0)' in sql
        assert 'check (tpm_limit is null or tpm_limit > 0)' in sql
        assert 'check (concurrency_limit is null or concurrency_limit > 0)' in sql
        assert 'check (warning_ratio > 0 and warning_ratio <= 1' in sql
        assert 'foreign key (application_id) references ai_application (application_id) on delete restrict' in sql
        assert 'foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict' in sql
        assert 'foreign key (platform_model_id) references ai_model_catalog (model_id) on delete restrict' in sql
        assert 'unique (api_key_id, platform_model_id)' in sql
        assert 'unique (application_id, idempotency_key)' in sql
        assert re.search(r'\bapi_key\s+varchar', sql) is None
        assert re.search(r'\bcomplete_key\b', sql) is None


def test_developer_api_entities_register_exact_tables_without_plaintext_key_columns() -> None:
    entities = _load_entities()

    assert set(entities) == DEVELOPER_API_TABLES
    assert set(Base.metadata.tables) >= DEVELOPER_API_TABLES
    api_key_table = entities['ai_api_key'].__table__
    assert api_key_table.c.api_key_id.primary_key is True
    assert api_key_table.c.api_key_id.type.length == RESOURCE_ID_LENGTH
    column_names = set(api_key_table.c.keys())
    assert {'secret_digest', 'digest_version', 'key_prefix', 'fingerprint'} <= column_names
    assert {'api_key', 'complete_key', 'secret'} & column_names == set()


def test_developer_api_entities_define_foreign_keys_checks_and_unique_keys() -> None:
    entities = _load_entities()
    application = entities['ai_application'].__table__
    api_key = entities['ai_api_key'].__table__
    grant = entities['ai_api_key_model_grant'].__table__
    quota = entities['ai_api_quota_policy'].__table__
    ledger = entities['ai_api_usage_ledger'].__table__

    _assert_restrict_foreign_key(api_key.c.application_id, 'ai_application.application_id')
    _assert_restrict_foreign_key(grant.c.api_key_id, 'ai_api_key.api_key_id')
    _assert_restrict_foreign_key(grant.c.platform_model_id, 'ai_model_catalog.model_id')
    _assert_restrict_foreign_key(quota.c.api_key_id, 'ai_api_key.api_key_id')
    _assert_restrict_foreign_key(ledger.c.application_id, 'ai_application.application_id')
    _assert_restrict_foreign_key(ledger.c.api_key_id, 'ai_api_key.api_key_id')

    assert "status in ('active', 'disabled')" in _constraint_sql(application).values()
    assert "status in ('active', 'retiring', 'revoked', 'expired')" in _constraint_sql(api_key).values()
    quota_checks = _constraint_sql(quota).values()
    assert 'rpm_limit is null or rpm_limit > 0' in quota_checks
    assert 'tpm_limit is null or tpm_limit > 0' in quota_checks
    assert 'concurrency_limit is null or concurrency_limit > 0' in quota_checks

    grant_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in grant.constraints
        if constraint.__class__.__name__ == 'UniqueConstraint'
    }
    ledger_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in ledger.constraints
        if constraint.__class__.__name__ == 'UniqueConstraint'
    }
    assert ('api_key_id', 'platform_model_id') in grant_unique_columns
    assert ('application_id', 'idempotency_key') in ledger_unique_columns


def test_developer_api_entities_compile_for_mysql_and_postgresql() -> None:
    entities = _load_entities()

    for entity in entities.values():
        for dialect in (mysql.dialect(), postgresql.dialect()):
            ddl = _normalize_sql(str(CreateTable(entity.__table__).compile(dialect=dialect)))
            assert ddl.startswith('create table')
            assert 'on delete restrict' in ddl or entity.__tablename__ == 'ai_application'

    ledger = entities['ai_api_usage_ledger'].__table__
    assert ledger.c.engine_model_snapshot.nullable is False
    assert ledger.c.cost_estimated.nullable is False
    assert ledger.c.raw_cost.type.precision == COST_PRECISION
    assert ledger.c.raw_cost.type.scale == COST_SCALE


def test_developer_api_entities_use_fresh_time_defaults_and_required_indexes() -> None:
    entities = _load_entities()

    for entity in entities.values():
        table = entity.__table__
        assert callable(table.c.created_at.default.arg)
        if 'updated_at' in table.c:
            assert callable(table.c.updated_at.default.arg)
            assert callable(table.c.updated_at.onupdate.arg)

    index_names = {index.name for entity in entities.values() for index in entity.__table__.indexes}
    assert {
        'idx_ai_application_owner_status',
        'idx_ai_api_key_application_status',
        'idx_ai_api_usage_key_started',
        'idx_ai_api_usage_status_started',
        'idx_ai_api_quota_bucket_window',
    } <= index_names
