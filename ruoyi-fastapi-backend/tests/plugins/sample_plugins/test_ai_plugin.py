from pathlib import Path

import pytest

from cli.runtime.plugin.scaffold import PluginFrontendVersionResolver
from plugins.core.discovery.registry import PluginRegistry
from plugins.core.discovery.scanner import PluginScanner
from plugins.core.management.entity.vo.schemas import PluginModel
from plugins.core.validation.structure import PluginStructureChecker

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = PROJECT_ROOT / 'ruoyi-fastapi-frontend'
FRONTEND_VERSION = PluginFrontendVersionResolver.resolve(FRONTEND_ROOT)

EXPECTED_AI_PERMISSIONS = {
    'ai:model:list',
    'ai:model:add',
    'ai:model:edit',
    'ai:model:remove',
    'ai:model:query',
    'ai:chat:list',
    'ai:runtime:query',
    'ai:model:publish',
    'ai:model:test',
    'ai:engine:credential:list',
    'ai:engine:credential:add',
    'ai:engine:credential:rotate',
    'ai:engine:credential:remove',
    'ai:engine:deployment:list',
    'ai:engine:deployment:query',
    'ai:engine:deployment:add',
    'ai:engine:deployment:edit',
    'ai:engine:deployment:remove',
    'ai:application:list',
    'ai:application:query',
    'ai:application:add',
    'ai:application:edit',
    'ai:application:disable',
    'ai:application:key:list',
    'ai:application:key:add',
    'ai:application:key:rotate',
    'ai:application:key:revoke',
}
EXPECTED_AI_PYTHON_DEPENDENCIES = [
    'httpx==0.28.1',
    'litellm==1.94.0',
]
EXPECTED_AI_VUE2_NPM_DEPENDENCIES = [
    '@antv/infographic^0.2.13',
    '@terrastruct/d2>=0.1.33',
    'katex>=0.16.27',
    'markstream-vue2^0.0.50',
    'mermaid>=11.15.0',
    'shiki^3.21.0',
    'stream-markdown>=0.0.16',
    'stream-monaco>=0.0.48',
]
EXPECTED_AI_VUE3_NPM_DEPENDENCIES = [
    '@antv/infographic^0.2.13',
    '@terrastruct/d2>=0.1.33',
    'katex>=0.16.27',
    'markstream-vue>=1.0.9-beta.2',
    'mermaid>=11.15.0',
    'shiki^3.21.0',
    'stream-diffs>=0.0.2',
    'stream-markdown>=0.0.16',
    'stream-monaco>=0.0.48',
]
EXPECTED_AI_VUE3_NPM_DEV_DEPENDENCIES = ['vite-plugin-monaco-editor-esm==2.0.2']


def test_ai_plugin_template_can_be_discovered() -> None:
    """校验仓库内 AI 插件可以被插件扫描器读取。"""
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(BACKEND_ROOT / 'plugins' / 'ai' / 'plugin.yaml')

    assert plugin.manifest.id == 'ai'
    assert plugin.manifest.version == '0.5.0'
    assert plugin.manifest.backend.module == 'plugins.ai'
    assert plugin.manifest.backend.hooks.on_startup == 'plugins.ai.hooks:on_startup'
    assert plugin.manifest.backend.hooks.on_shutdown == 'plugins.ai.hooks:on_shutdown'
    assert plugin.manifest.backend.migrations == [
        'migrations/mysql/001_init.sql',
        'migrations/mysql/002_model_service_foundation.sql',
        'migrations/mysql/003_platform_chat.sql',
        'migrations/mysql/004_developer_api_foundation.sql',
        'migrations/postgresql/001_init.sql',
        'migrations/postgresql/002_model_service_foundation.sql',
        'migrations/postgresql/003_platform_chat.sql',
        'migrations/postgresql/004_developer_api_foundation.sql',
    ]
    assert plugin.manifest.backend.seeds == [
        'seeds/mysql/ai_provider_type.sql',
        'seeds/postgresql/ai_provider_type.sql',
    ]
    assert plugin.manifest.frontend.menus[0].children[0].name == '模型中心'
    assert plugin.manifest.frontend.menus[0].children[0].component == 'plugin/ai/model/index'
    assert plugin.manifest.frontend.menus[0].children[1].component == 'plugin/ai/chat/index'
    assert set(plugin.manifest.permission_codes) == EXPECTED_AI_PERMISSIONS
    assert plugin.manifest.permission_name_map['ai:model:add'] == '新增模型'
    assert plugin.manifest.permission_name_map['ai:model:publish'] == '发布模型'
    assert plugin.manifest.permission_name_map['ai:model:test'] == '测试模型连接'
    assert plugin.manifest.permission_name_map['ai:engine:credential:rotate'] == '轮换引擎凭据'
    assert plugin.manifest.permission_name_map['ai:engine:deployment:remove'] == '删除引擎部署'
    assert plugin.manifest.dependencies.python == EXPECTED_AI_PYTHON_DEPENDENCIES
    assert plugin.manifest.config.items == []


@pytest.mark.skipif(FRONTEND_VERSION != 'vue2', reason='当前项目不是 Vue 2 前端')
def test_ai_plugin_vue2_frontend_dependencies() -> None:
    """校验 Vue 2 项目的 AI 插件声明 Vue 2 专属前端依赖。"""
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(BACKEND_ROOT / 'plugins' / 'ai' / 'plugin.yaml')

    assert plugin.manifest.dependencies.npm == EXPECTED_AI_VUE2_NPM_DEPENDENCIES
    assert plugin.manifest.dependencies.npm_dev == []


@pytest.mark.skipif(FRONTEND_VERSION != 'vue3', reason='当前项目不是 Vue 3 前端')
def test_ai_plugin_vue3_frontend_dependencies() -> None:
    """校验 Vue 3 项目的 AI 插件声明 Vue 3 专属前端依赖。"""
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(BACKEND_ROOT / 'plugins' / 'ai' / 'plugin.yaml')

    assert plugin.manifest.dependencies.npm == EXPECTED_AI_VUE3_NPM_DEPENDENCIES
    assert plugin.manifest.dependencies.npm_dev == EXPECTED_AI_VUE3_NPM_DEV_DEPENDENCIES


def test_ai_plugin_runtime_paths_exist() -> None:
    """校验 AI 插件后端和前端运行路径存在。"""
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(BACKEND_ROOT / 'plugins' / 'ai' / 'plugin.yaml')
    registry = PluginRegistry.build(
        [plugin],
        [
            PluginModel(
                pluginId='ai',
                pluginName='AI 管理',
                version='0.5.0',
                installedVersion='0.5.0',
                enabled='0',
                status='installed',
            )
        ],
    )

    assert registry.get_enabled_controller_dirs() == [BACKEND_ROOT / 'plugins' / 'ai' / 'controller']
    assert registry.get_enabled_entity_do_dirs() == [BACKEND_ROOT / 'plugins' / 'ai' / 'entity' / 'do']
    assert (BACKEND_ROOT / 'plugins' / 'ai' / 'controller').is_dir()
    assert (BACKEND_ROOT / 'plugins' / 'ai' / 'entity' / 'do').is_dir()
    assert (FRONTEND_ROOT / 'plugins' / 'ai' / 'views' / 'model' / 'index.vue').is_file()
    assert (FRONTEND_ROOT / 'plugins' / 'ai' / 'views' / 'chat' / 'index.vue').is_file()
    assert not (FRONTEND_ROOT / 'src' / 'api' / 'ai').exists()
    assert not (FRONTEND_ROOT / 'src' / 'views' / 'ai').exists()


def test_ai_plugin_structure_check_passes() -> None:
    """校验 AI 插件目录满足插件结构检查。"""
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(BACKEND_ROOT / 'plugins' / 'ai' / 'plugin.yaml')
    result = PluginStructureChecker(BACKEND_ROOT).check(plugin)

    assert result.ok is True
    assert result.failed_items == []


def test_ai_plugin_sql_assets_own_ai_schema_and_seed_data() -> None:
    """校验 AI 插件通过 SQL migration 和 seed 管理自身数据。"""
    plugin_root = BACKEND_ROOT / 'plugins' / 'ai'
    mysql_migration = (plugin_root / 'migrations' / 'mysql' / '001_init.sql').read_text(encoding='utf-8')
    postgres_migration = (plugin_root / 'migrations' / 'postgresql' / '001_init.sql').read_text(encoding='utf-8')
    mysql_seed = (plugin_root / 'seeds' / 'mysql' / 'ai_provider_type.sql').read_text(encoding='utf-8')
    postgres_seed = (plugin_root / 'seeds' / 'postgresql' / 'ai_provider_type.sql').read_text(encoding='utf-8')

    for sql_content in (mysql_migration, postgres_migration):
        assert '-- AI模型表' in sql_content
        assert '-- AI对话配置表' in sql_content
        assert 'create table ai_models' in sql_content
        assert 'create table ai_chat_config' in sql_content
    for sql_content in (mysql_seed, postgres_seed):
        assert '-- 初始化-字典类型表数据' in sql_content
        assert '-- 初始化-字典数据表数据' in sql_content
        assert 'ai_provider_type' in sql_content
        assert 'OpenAI' in sql_content
    assert 'sysdate()' in mysql_seed
    assert 'current_timestamp' not in mysql_seed
    assert 'current_timestamp' in postgres_seed
    assert 'sysdate()' not in postgres_seed
    assert 'auto_increment' in mysql_migration
    assert 'engine=innodb' in mysql_migration
    assert 'bigserial' not in mysql_migration
    assert 'comment on column' not in mysql_migration
    assert 'bigserial' in postgres_migration
    assert 'comment on column' in postgres_migration
    assert 'auto_increment' not in postgres_migration
    assert 'engine=innodb' not in postgres_migration
