from pathlib import Path
import re

from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.core.discovery.scanner import PluginScanner


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'ai'


def _read_migration(database: str) -> str:
    return (PLUGIN_ROOT / 'migrations' / database / '002_model_service_foundation.sql').read_text(encoding='utf-8')


def _normalize_sql(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql.lower()).strip()


def test_model_service_migrations_are_registered_after_initial_schema() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    assert plugin.manifest.version == '0.3.0'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_init.sql',
        'migrations/mysql/002_model_service_foundation.sql',
        'migrations/postgresql/001_init.sql',
        'migrations/postgresql/002_model_service_foundation.sql',
    ]


def test_model_service_migrations_define_only_additive_platform_tables() -> None:
    mysql_sql = _normalize_sql(_read_migration('mysql'))
    postgres_sql = _normalize_sql(_read_migration('postgresql'))

    for sql in (mysql_sql, postgres_sql):
        assert 'drop table' not in sql
        assert 'create table ai_model_catalog' in sql
        assert 'create table ai_resource_binding' in sql
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

    assert 'capabilities json not null' in mysql_sql
    assert 'default_parameters json not null' in mysql_sql
    assert 'engine_metadata json not null' in mysql_sql
    assert 'capabilities jsonb not null' in postgres_sql
    assert 'default_parameters jsonb not null' in postgres_sql
    assert 'engine_metadata jsonb not null' in postgres_sql


def test_model_catalog_entity_matches_database_contract() -> None:
    assert AiModelCatalog.__tablename__ == 'ai_model_catalog'
    assert AiModelCatalog.__table__.c.model_id.primary_key is True
    assert AiModelCatalog.__table__.c.model_id.type.length == 36
    assert AiModelCatalog.__table__.c.model_code.unique is True
    assert AiModelCatalog.__table__.c.capabilities.nullable is False
    assert AiModelCatalog.__table__.c.default_parameters.nullable is False
    assert AiModelCatalog.__table__.c.lifecycle_status.nullable is False
    assert AiModelCatalog.__table__.c.lifecycle_status.server_default is not None
    assert AiModelCatalog.__table__.c.create_time.default.arg is not None
    assert AiModelCatalog.__table__.c.update_time.onupdate.arg is not None
    capabilities_default = AiModelCatalog.__table__.c.capabilities.default.arg
    parameters_default = AiModelCatalog.__table__.c.default_parameters.default.arg
    assert callable(capabilities_default)
    assert callable(parameters_default)
    assert capabilities_default(None) == {}
    assert parameters_default(None) == {}
    assert capabilities_default(None) is not capabilities_default(None)
    assert parameters_default(None) is not parameters_default(None)


def test_resource_binding_entity_matches_database_contract() -> None:
    assert AiResourceBinding.__tablename__ == 'ai_resource_binding'
    assert AiResourceBinding.__table__.c.binding_id.primary_key is True
    assert AiResourceBinding.__table__.c.binding_id.type.length == 36
    assert AiResourceBinding.__table__.c.resource_id.type.length == 36
    assert AiResourceBinding.__table__.c.engine_metadata.nullable is False
    assert AiResourceBinding.__table__.c.version.nullable is False
    assert AiResourceBinding.__table__.c.version.server_default is not None
    assert AiResourceBinding.__table__.c.version.default.arg == 1
    metadata_default = AiResourceBinding.__table__.c.engine_metadata.default.arg
    assert callable(metadata_default)
    assert metadata_default(None) == {}
    assert metadata_default(None) is not metadata_default(None)

    foreign_keys = AiResourceBinding.__table__.c.resource_id.foreign_keys
    assert len(foreign_keys) == 1
    foreign_key = next(iter(foreign_keys))
    assert str(foreign_key.target_fullname) == 'ai_model_catalog.model_id'
    assert foreign_key.ondelete == 'RESTRICT'


def test_model_service_migration_does_not_change_legacy_schema_files() -> None:
    for database in ('mysql', 'postgresql'):
        initial = (PLUGIN_ROOT / 'migrations' / database / '001_init.sql').read_text(encoding='utf-8').lower()
        assert 'create table ai_models' in initial
        assert 'create table ai_chat_config' in initial
