from pathlib import Path

from plugins.core.discovery.scanner import PluginScanner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'agent_extension'
EXPECTED_PERMISSIONS = {
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
    assert plugin.manifest.version == '0.1.0'
    assert plugin.manifest.backend.module == 'plugins.agent_extension'
    assert plugin.manifest.backend.hooks.on_startup == 'plugins.agent_extension.hooks:on_startup'
    assert plugin.manifest.backend.hooks.on_shutdown == 'plugins.agent_extension.hooks:on_shutdown'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_skill_management.sql',
        'migrations/postgresql/001_skill_management.sql',
    ]
    root_menu = plugin.manifest.frontend.menus[0]
    assert root_menu.name == '智能体扩展'
    assert root_menu.path == 'extensions'
    assert len(root_menu.children) == 1
    assert root_menu.children[0].name == '技能管理'
    assert root_menu.children[0].component == 'plugin/agent_extension/skill/index'
    assert root_menu.children[0].perms == 'agent_extension:skill:list'
    assert set(plugin.manifest.permission_codes) == EXPECTED_PERMISSIONS


def test_agent_extension_manifest_does_not_ship_knowledge_placeholder() -> None:
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')

    route_names = [menu.route_name for menu in plugin.manifest.frontend.menus[0].children]
    assert route_names == ['AgentExtensionSkill']

