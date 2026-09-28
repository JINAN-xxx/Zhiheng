import re
from pathlib import Path

from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.core.discovery.scanner import PluginScanner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'ai'
MODEL_SERVICE_TABLE_COUNT = 2
RESOURCE_ID_LENGTH = 36


def _read_migration(database: str) -> str:
    return (PLUGIN_ROOT / 'migrations' / database / '002_model_service_foundation.sql').read_text(encoding='utf-8')


def _normalize_sql(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql.lower()).strip()


def test_model_service_migrations_are_registered_after_initial_schema() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    assert plugin.manifest.version == '0.9.1'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_init.sql',
        'migrations/mysql/002_model_service_foundation.sql',
        'migrations/mysql/003_platform_chat.sql',
        'migrations/mysql/004_developer_api_foundation.sql',
        'migrations/mysql/005_embedding_rerank_gateway.sql',
        'migrations/mysql/006_quota_reservation.sql',
        'migrations/mysql/007_application_lifecycle.sql',
        'migrations/mysql/008_chat_skill_snapshots.sql',
        'migrations/mysql/009_chat_knowledge.sql',
        'migrations/postgresql/001_init.sql',
        'migrations/postgresql/002_model_service_foundation.sql',
        'migrations/postgresql/003_platform_chat.sql',
        'migrations/postgresql/004_developer_api_foundation.sql',
        'migrations/postgresql/005_embedding_rerank_gateway.sql',
        'migrations/postgresql/006_quota_reservation.sql',
        'migrations/postgresql/007_application_lifecycle.sql',
        'migrations/postgresql/008_chat_skill_snapshots.sql',
        'migrations/postgresql/009_chat_knowledge.sql',
    ]


def test_model_service_migrations_define_only_additive_platform_tables() -> None:
    mysql_sql = _normalize_sql(_read_migration('mysql'))
    postgres_sql = _normalize_sql(_read_migration('postgresql'))

    for sql in (mysql_sql, postgres_sql):
        assert 'drop table' not in sql
        assert 'create table if not exists ai_model_catalog' in sql
        assert 'create table if not exists ai_resource_binding' in sql
        assert 'model_id varchar(36)' in sql
        assert 'binding_id varchar(36)' in sql
        assert 'model_code varchar(100) not null' in sql
        assert 'unique (model_code)' in sql
        assert 'unique (resource_type, resource_id)' in sql
        assert 'lifecycle_status' in sql
        assert "check (lifecycle_status in ('draft', 'active', 'disabled'))" in sql
        assert 'version integer not null' in sql or 'version int not null' in sql
        assert 'check (version > 0)' in sql
        assert 'foreign key (resource_id) references ai_model_catalog (model_id) on delete restrict' in sql
        assert "check (resource_type = 'model')" in sql
        assert "check (engine_type = 'litellm')" in sql

    assert 'capabilities json not null' in mysql_sql
    assert 'default_parameters json not null' in mysql_sql
    assert 'engine_metadata json not null' in mysql_sql
    assert 'capabilities jsonb not null' in postgres_sql
    assert 'default_parameters jsonb not null' in postgres_sql
    assert 'engine_metadata jsonb not null' in postgres_sql


def test_model_service_migrations_support_tables_precreated_by_metadata() -> None:
    mysql_sql = _normalize_sql(_read_migration('mysql'))
    postgres_sql = _normalize_sql(_read_migration('postgresql'))

    assert mysql_sql.count('create table if not exists') == MODEL_SERVICE_TABLE_COUNT
    assert 'create index if not exists' not in mysql_sql
    assert 'index idx_ai_model_catalog_lifecycle (lifecycle_status)' in mysql_sql
    assert 'index idx_ai_resource_binding_engine (engine_type, engine_resource_id)' in mysql_sql
    assert 'create index idx_ai_model_catalog_lifecycle' not in mysql_sql
    assert 'create index idx_ai_resource_binding_engine' not in mysql_sql

    assert postgres_sql.count('create table if not exists') == MODEL_SERVICE_TABLE_COUNT
    assert 'create index if not exists idx_ai_model_catalog_lifecycle' in postgres_sql
    assert 'create index if not exists idx_ai_resource_binding_engine' in postgres_sql


def test_model_catalog_entity_matches_database_contract() -> None:
    assert AiModelCatalog.__tablename__ == 'ai_model_catalog'
    assert AiModelCatalog.__table__.c.model_id.primary_key is True
    assert AiModelCatalog.__table__.c.model_id.type.length == RESOURCE_ID_LENGTH
    assert AiModelCatalog.__table__.c.model_code.unique is True
    assert AiModelCatalog.__table__.c.capabilities.nullable is False
    assert AiModelCatalog.__table__.c.default_parameters.nullable is False
    assert AiModelCatalog.__table__.c.lifecycle_status.nullable is False
    assert AiModelCatalog.__table__.c.lifecycle_status.server_default is not None
    assert callable(AiModelCatalog.__table__.c.create_time.default.arg)
    assert callable(AiModelCatalog.__table__.c.update_time.default.arg)
    assert callable(AiModelCatalog.__table__.c.update_time.onupdate.arg)
    capabilities_default = AiModelCatalog.__table__.c.capabilities.default.arg
    parameters_default = AiModelCatalog.__table__.c.default_parameters.default.arg
    assert callable(capabilities_default)
    assert callable(parameters_default)
    assert capabilities_default(None) == {}
    assert parameters_default(None) == {}
    assert capabilities_default(None) is not capabilities_default(None)
    assert parameters_default(None) is not parameters_default(None)


def test_model_catalog_audit_defaults_compile_as_empty_strings() -> None:
    for dialect in (mysql.dialect(), postgresql.dialect()):
        ddl = str(CreateTable(AiModelCatalog.__table__).compile(dialect=dialect))
        normalized_ddl = _normalize_sql(ddl)

        assert re.search(r"create_by varchar\(64\).*default ''", normalized_ddl)
        assert re.search(r"update_by varchar\(64\).*default ''", normalized_ddl)
        assert "default ''''''" not in normalized_ddl


def test_model_catalog_metadata_matches_database_specific_json_and_indexes() -> None:
    mysql_ddl = _normalize_sql(str(CreateTable(AiModelCatalog.__table__).compile(dialect=mysql.dialect())))
    postgres_ddl = _normalize_sql(str(CreateTable(AiModelCatalog.__table__).compile(dialect=postgresql.dialect())))

    assert 'capabilities json not null' in mysql_ddl
    assert 'default_parameters json not null' in mysql_ddl
    assert 'capabilities jsonb not null' in postgres_ddl
    assert 'default_parameters jsonb not null' in postgres_ddl
    assert {index.name for index in AiModelCatalog.__table__.indexes} >= {'idx_ai_model_catalog_lifecycle'}


def test_resource_binding_entity_matches_database_contract() -> None:
    assert AiResourceBinding.__tablename__ == 'ai_resource_binding'
    assert AiResourceBinding.__table__.c.binding_id.primary_key is True
    assert AiResourceBinding.__table__.c.binding_id.type.length == RESOURCE_ID_LENGTH
    assert AiResourceBinding.__table__.c.resource_id.type.length == RESOURCE_ID_LENGTH
    assert AiResourceBinding.__table__.c.engine_metadata.nullable is False
    assert AiResourceBinding.__table__.c.version.nullable is False
    assert AiResourceBinding.__table__.c.version.server_default is not None
    assert AiResourceBinding.__table__.c.version.default.arg == 1
    metadata_default = AiResourceBinding.__table__.c.engine_metadata.default.arg
    assert callable(metadata_default)
    assert metadata_default(None) == {}
    assert metadata_default(None) is not metadata_default(None)
    assert callable(AiResourceBinding.__table__.c.create_time.default.arg)
    assert callable(AiResourceBinding.__table__.c.update_time.default.arg)
    assert callable(AiResourceBinding.__table__.c.update_time.onupdate.arg)

    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in AiResourceBinding.__table__.constraints
        if hasattr(constraint, 'sqltext')
    }
    assert check_constraints['ck_ai_resource_binding_resource_type'] == "resource_type = 'model'"
    assert check_constraints['ck_ai_resource_binding_engine_type'] == "engine_type = 'litellm'"

    foreign_keys = AiResourceBinding.__table__.c.resource_id.foreign_keys
    assert len(foreign_keys) == 1
    foreign_key = next(iter(foreign_keys))
    assert str(foreign_key.target_fullname) == 'ai_model_catalog.model_id'
    assert foreign_key.ondelete == 'RESTRICT'


def test_resource_binding_audit_defaults_compile_as_empty_strings() -> None:
    for dialect in (mysql.dialect(), postgresql.dialect()):
        ddl = str(CreateTable(AiResourceBinding.__table__).compile(dialect=dialect))
        normalized_ddl = _normalize_sql(ddl)

        assert re.search(r"create_by varchar\(64\).*default ''", normalized_ddl)
        assert re.search(r"update_by varchar\(64\).*default ''", normalized_ddl)
        assert "default ''''''" not in normalized_ddl


def test_resource_binding_metadata_matches_database_specific_json_and_indexes() -> None:
    mysql_ddl = _normalize_sql(str(CreateTable(AiResourceBinding.__table__).compile(dialect=mysql.dialect())))
    postgres_ddl = _normalize_sql(str(CreateTable(AiResourceBinding.__table__).compile(dialect=postgresql.dialect())))

    assert 'engine_metadata json not null' in mysql_ddl
    assert 'engine_metadata jsonb not null' in postgres_ddl
    assert {index.name for index in AiResourceBinding.__table__.indexes} >= {'idx_ai_resource_binding_engine'}

    for table in (AiModelCatalog.__table__, AiResourceBinding.__table__):
        postgres_indexes = [
            _normalize_sql(str(CreateIndex(index).compile(dialect=postgresql.dialect()))) for index in table.indexes
        ]
        assert postgres_indexes
        assert all(index_ddl.startswith('create index') for index_ddl in postgres_indexes)


def test_model_service_migration_does_not_change_legacy_schema_files() -> None:
    for database in ('mysql', 'postgresql'):
        initial = (PLUGIN_ROOT / 'migrations' / database / '001_init.sql').read_text(encoding='utf-8').lower()
        assert 'create table ai_models' in initial
        assert 'create table ai_chat_config' in initial
