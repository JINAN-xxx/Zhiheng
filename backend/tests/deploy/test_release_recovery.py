import importlib
import json
import sys
from pathlib import Path

import pytest

from tests.deploy.test_release_manager import bundle, setup_site


def test_missing_state_cannot_bypass_compatibility_after_containers_removed(tmp_path: Path) -> None:
    _, runtime, manager, site = setup_site(tmp_path)
    before = (site / '.env').read_bytes()
    (site / 'state.json').unlink()
    runtime.calls.clear()
    with pytest.raises(Exception, match='状态'):
        manager.deploy(bundle(tmp_path / 'bundle2', 'v2', contract='d'))
    assert runtime.calls == []
    assert (site / '.env').read_bytes() == before


def test_manual_rollback_accepts_runtime_plugin_error_without_data_drift(tmp_path: Path) -> None:
    _, runtime, manager, _ = setup_site(tmp_path)
    manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    snapshot = runtime.snapshot_plugins()
    snapshot['plugins'][0]['status'] = 'error'
    runtime.recovery_snapshot = lambda: snapshot
    runtime.snapshot_plugins = lambda: pytest.fail('rollback must not require healthy current plugins')
    manager.rollback()
    assert manager.status()['current']['version'] == 'v1'


@pytest.mark.parametrize('field,value', [('identity', None), ('identity', ''), ('site_id', ''), ('format', True)])
def test_damaged_state_cannot_regenerate_missing_configuration(tmp_path: Path, field: str, value: object) -> None:
    _, runtime, manager, site = setup_site(tmp_path)
    path = site / 'state.json'
    state = json.loads(path.read_text())
    state[field] = value
    path.write_text(json.dumps(state))
    (site / '.env').unlink()
    runtime.calls.clear()
    with pytest.raises(Exception, match='状态'):
        manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    assert runtime.calls == []
    assert not (site / '.env').exists()


def test_recovery_stops_only_owned_running_oneoff_writers(tmp_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'deploy'))
    runtime_module = importlib.import_module('release.runtime')
    runtime = runtime_module.DockerRuntime(tmp_path)
    runtime.site_id = 'our-site'
    calls = []
    runtime.run = lambda command, **kwargs: calls.append(command) or ''
    runtime.containers = lambda: [
        {
            'Id': name,
            'Config': {
                'Labels': {
                    'io.zhiheng.installation': site,
                    'com.docker.compose.service': service,
                    'com.docker.compose.oneoff': oneoff,
                }
            },
            'State': {'Status': 'running'},
        }
        for name, site, service, oneoff in (
            ('orphan-init', 'our-site', 'platform-init', 'True'),
            ('main-backend', 'our-site', 'zhiheng-backend-pg', 'False'),
            ('foreign-init', 'other-site', 'platform-init', 'True'),
        )
    ]
    runtime.quiesce_operations()
    assert len(calls) == 1
    assert calls[0][-1] == 'orphan-init'
    assert calls[0][1] == 'stop'


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_rollback_preserves_plugin_enable_changes_since_first_release(tmp_path: Path, enabled: str) -> None:
    _, runtime, manager, _ = setup_site(tmp_path)
    baseline = runtime.snapshot_plugins()
    baseline['plugins'][0]['enabled'] = '1'
    runtime.snapshot_plugins = lambda: baseline
    manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    # An operator can change plugin enablement in either version. Rollback must
    # preserve the current choice while still rejecting version/migration drift.
    enabled_now = {'plugins': [dict(row, enabled=enabled) for row in baseline['plugins']], 'migrations': []}
    runtime.recovery_snapshot = lambda: enabled_now
    recovered = []
    runtime.restore_plugin_state = recovered.append
    manager.rollback()
    assert recovered[0]['plugins'][0]['enabled'] == enabled
    assert manager.status()['current']['snapshot']['plugins'][0]['enabled'] == enabled
