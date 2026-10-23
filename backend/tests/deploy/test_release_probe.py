import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def probe_module() -> ModuleType:
    sys.path.insert(0, str(ROOT / 'deploy'))
    assert (ROOT / 'deploy/release/probe.py').is_file(), '发布插件健康校验尚未实现'
    return importlib.import_module('release.probe')


def healthy() -> tuple[dict, dict]:
    plugins = [
        {'plugin_id': plugin, 'version': '1', 'installed_version': '1', 'enabled': '0', 'status': 'installed'}
        for plugin in ('ai', 'agent_extension')
    ]
    migrations = [
        {
            'plugin_id': 'ai',
            'migration_path': 'migrations/postgresql/001.sql',
            'migration_checksum': 'checksum',
            'version': '1',
            'status': 'success',
        }
    ]
    definitions = {
        'ai': {'version': '1', 'migrations': {'migrations/postgresql/001.sql': 'checksum'}},
        'agent_extension': {'version': '1', 'migrations': {}},
    }
    return {'plugins': plugins, 'migrations': migrations}, definitions


def test_plugin_error_is_unhealthy_even_when_http_works() -> None:
    module = probe_module()
    snapshot, definitions = healthy()
    module.validate_snapshot(snapshot, definitions)
    snapshot['plugins'][0]['status'] = 'error'
    with pytest.raises(ValueError, match='插件'):
        module.validate_snapshot(snapshot, definitions)


@pytest.mark.parametrize('change', ['missing', 'checksum', 'version', 'failed', 'unexpected'])
def test_database_drift_is_rejected_before_fast_update(change: str) -> None:
    module = probe_module()
    snapshot, definitions = healthy()
    if change == 'missing':
        snapshot['migrations'] = []
    elif change == 'checksum':
        snapshot['migrations'][0]['migration_checksum'] = 'other'
    elif change == 'version':
        snapshot['plugins'][0]['installed_version'] = 'old'
    elif change == 'failed':
        snapshot['migrations'][0]['status'] = 'failed'
    else:
        snapshot['migrations'].append(dict(snapshot['migrations'][0], migration_path='unknown.sql'))
    with pytest.raises(ValueError):
        module.validate_snapshot(snapshot, definitions)


def test_recovery_permits_runtime_errors_but_not_changed_data_or_enable_state() -> None:
    module = probe_module()
    before, _definitions = healthy()
    current, _ = healthy()
    current['plugins'][0]['status'] = 'error'
    module.validate_recovery(before, current)
    current['plugins'][0]['enabled'] = '1'
    with pytest.raises(ValueError):
        module.validate_recovery(before, current)


def test_route_gate_requires_enabled_plugins_and_crypto() -> None:
    module = probe_module()
    snapshot, _ = healthy()
    paths = {p: {'get': {}} for p in module.REQUIRED_ROUTES['ai'] + module.REQUIRED_ROUTES['agent_extension']}
    paths['/transport/crypto/frontend-config'] = {'get': {}}
    module.validate_routes(paths, snapshot)
    paths.pop('/agent_extension/agents')
    with pytest.raises(ValueError, match='路由'):
        module.validate_routes(paths, snapshot)
