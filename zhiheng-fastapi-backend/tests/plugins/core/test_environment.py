from pathlib import Path

import pytest

from plugins.core.environment import PluginRuntimeEnvironmentService


@pytest.fixture(autouse=True)
def clear_project_root_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for prefix in ('ZHIHENG', 'RUOYI'):
        for suffix in ('PLUGIN_BACKEND_ROOT', 'BACKEND_ROOT', 'PLUGIN_FRONTEND_ROOT', 'FRONTEND_ROOT'):
            monkeypatch.delenv(f'{prefix}_{suffix}', raising=False)


@pytest.mark.parametrize('scope', ['PLUGIN_', ''])
def test_environment_reads_zhiheng_project_roots(scope: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backend_root = tmp_path / 'api'
    frontend_root = tmp_path / 'web'
    monkeypatch.setenv(f'ZHIHENG_{scope}BACKEND_ROOT', str(backend_root))
    monkeypatch.setenv(f'ZHIHENG_{scope}FRONTEND_ROOT', str(frontend_root))

    environment = PluginRuntimeEnvironmentService()

    assert environment.get_backend_dir() == str(backend_root.resolve())
    assert environment.get_frontend_dir() == str(frontend_root.resolve())


@pytest.mark.parametrize('scope', ['PLUGIN_', ''])
def test_zhiheng_project_roots_override_legacy_environment(
    scope: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend_root = tmp_path / 'api'
    frontend_root = tmp_path / 'web'
    for legacy_scope in ('PLUGIN_', ''):
        monkeypatch.setenv(f'RUOYI_{legacy_scope}BACKEND_ROOT', str(tmp_path / 'legacy-api'))
        monkeypatch.setenv(f'RUOYI_{legacy_scope}FRONTEND_ROOT', str(tmp_path / 'legacy-web'))
    monkeypatch.setenv(f'ZHIHENG_{scope}BACKEND_ROOT', str(backend_root))
    monkeypatch.setenv(f'ZHIHENG_{scope}FRONTEND_ROOT', str(frontend_root))

    environment = PluginRuntimeEnvironmentService()

    assert environment.get_backend_dir() == str(backend_root.resolve())
    assert environment.get_frontend_dir() == str(frontend_root.resolve())


@pytest.mark.parametrize('scope', ['PLUGIN_', ''])
def test_legacy_project_roots_remain_fallbacks(scope: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backend_root = tmp_path / 'existing-api'
    frontend_root = tmp_path / 'existing-web'
    monkeypatch.setenv('ZHIHENG_PLUGIN_BACKEND_ROOT', ' ')
    monkeypatch.setenv('ZHIHENG_PLUGIN_FRONTEND_ROOT', ' ')
    monkeypatch.setenv(f'RUOYI_{scope}BACKEND_ROOT', str(backend_root))
    monkeypatch.setenv(f'RUOYI_{scope}FRONTEND_ROOT', str(frontend_root))

    environment = PluginRuntimeEnvironmentService()

    assert environment.get_backend_dir() == str(backend_root.resolve())
    assert environment.get_frontend_dir() == str(frontend_root.resolve())


def test_explicit_project_roots_override_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('ZHIHENG_PLUGIN_BACKEND_ROOT', str(tmp_path / 'configured-api'))
    monkeypatch.setenv('ZHIHENG_PLUGIN_FRONTEND_ROOT', str(tmp_path / 'configured-web'))
    backend_root = tmp_path / 'explicit-api'
    frontend_root = tmp_path / 'explicit-web'

    environment = PluginRuntimeEnvironmentService(backend_root=backend_root, frontend_root=frontend_root)

    assert environment.get_backend_dir() == str(backend_root.resolve())
    assert environment.get_frontend_dir() == str(frontend_root.resolve())
