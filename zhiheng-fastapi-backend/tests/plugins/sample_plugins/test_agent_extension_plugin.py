import sqlite3
from pathlib import Path

import pytest

from plugins.core.discovery.scanner import PluginScanner
from plugins.core.validation.structure import PluginStructureChecker

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'agent_extension'
EXPECTED_MENU_COUNT = 2
EXPECTED_PERMISSIONS = {
    'agent_extension:agent:use',
    'agent_extension:work:list',
    'agent_extension:work:query',
    'agent_extension:work:add',
    'agent_extension:work:edit',
    'agent_extension:work:remove',
    'agent_extension:work:run',
    'agent_extension:agent:list',
    'agent_extension:agent:query',
    'agent_extension:agent:add',
    'agent_extension:agent:edit',
    'agent_extension:agent:remove',
    'agent_extension:agent:publish',
    'agent_extension:agent:test',
    'agent_extension:skill:list',
    'agent_extension:skill:query',
    'agent_extension:skill:add',
    'agent_extension:skill:edit',
    'agent_extension:skill:remove',
    'agent_extension:skill:use',
}


def test_agent_extension_manifest_declares_skill_menu_and_permissions() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    assert plugin.manifest.id == 'agent_extension'
    assert plugin.manifest.version == '0.4.0'
    assert plugin.manifest.backend.module == 'plugins.agent_extension'
    assert plugin.manifest.backend.hooks.on_startup == 'plugins.agent_extension.hooks:on_startup'
    assert plugin.manifest.backend.hooks.on_shutdown == 'plugins.agent_extension.hooks:on_shutdown'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_skill_management.sql',
        'migrations/postgresql/001_skill_management.sql',
        'migrations/mysql/002_agent_management.sql',
        'migrations/postgresql/002_agent_management.sql',
        'migrations/mysql/003_agent_welcome_message.sql',
        'migrations/postgresql/003_agent_welcome_message.sql',
        'migrations/mysql/004_work_tasks.sql',
        'migrations/postgresql/004_work_tasks.sql',
    ]
    root_menu = plugin.manifest.frontend.menus[0]
    assert root_menu.name == '智能体扩展'
    assert root_menu.path == 'extensions'
    assert len(root_menu.children) == EXPECTED_MENU_COUNT
    assert root_menu.children[0].name == '智能体管理'
    assert root_menu.children[0].component == 'plugin/agent_extension/agent/index'
    assert root_menu.children[0].perms == 'agent_extension:agent:list'
    assert root_menu.children[1].name == '技能管理'
    assert root_menu.children[1].component == 'plugin/agent_extension/skill/index'
    assert root_menu.children[1].perms == 'agent_extension:skill:list'
    assert plugin.manifest.backend.routers.public_prefixes == ['/v1/agents']
    assert [dependency.id for dependency in plugin.manifest.dependencies.plugins] == ['ai']
    assert set(plugin.manifest.permission_codes) == EXPECTED_PERMISSIONS


def test_agent_extension_manifest_does_not_ship_knowledge_placeholder() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    route_names = [menu.route_name for menu in plugin.manifest.frontend.menus[0].children]
    assert route_names == ['AgentExtensionAgent', 'AgentExtensionSkill']


@pytest.mark.parametrize('dialect', ['mysql', 'postgresql'])
def test_welcome_message_migration_preserves_existing_rows_and_defaults_new_rows(dialect: str) -> None:
    migration = PLUGIN_ROOT / 'migrations' / dialect / '003_agent_welcome_message.sql'
    assert migration.is_file()
    with sqlite3.connect(':memory:') as connection:
        connection.execute('create table agent_configuration (agent_id varchar(36) primary key, name varchar(100))')
        connection.executemany(
            'insert into agent_configuration (agent_id, name) values (?, ?)',
            [('existing-agent', 'Existing agent'), ('second-agent', 'Second agent')],
        )
        connection.executescript(migration.read_text(encoding='utf-8'))
        assert connection.execute(
            'select agent_id, name, welcome_message from agent_configuration order by agent_id'
        ).fetchall() == [('existing-agent', 'Existing agent', ''), ('second-agent', 'Second agent', '')]
        connection.execute("insert into agent_configuration (agent_id, name) values ('new-agent', 'New agent')")
        assert connection.execute(
            "select welcome_message from agent_configuration where agent_id = 'new-agent'"
        ).fetchone() == ('',)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("update agent_configuration set welcome_message = null where agent_id = 'new-agent'")


def test_agent_extension_routes_and_frontend_references_pass_structure_checks() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')
    result = PluginStructureChecker(
        BACKEND_ROOT,
        BACKEND_ROOT.parent / 'zhiheng-fastapi-frontend' / 'plugins',
    ).check(plugin)
    assert result.ok, result.failed_items
