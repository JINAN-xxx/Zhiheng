"""Exercise the real release state machine and probe guards without Docker."""

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.deploy.test_release_manager import Runtime, bundle, manager_module
from tests.deploy.test_release_probe import healthy, probe_module

if TYPE_CHECKING:
    from collections.abc import Callable


class DatabaseRuntime(Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.database, self.definitions = healthy()
        self.running = False
        self.orphan_writing = False
        self.hooks: dict[str, Callable[[], None]] = {}
        self.frozen_snapshots = []

    def checkpoint(self, name: str) -> None:
        hook = self.hooks.pop(name, None)
        if hook:
            hook()

    def set_enabled(self, value: str) -> None:
        self.database['plugins'][1]['enabled'] = value

    def start_infrastructure(self) -> None:
        super().start_infrastructure()
        self.checkpoint('infrastructure')

    def stop_applications(self) -> None:
        self.checkpoint('before_stop')
        super().stop_applications()
        self.running = False
        self.checkpoint('stopped')

    def quiesce_operations(self) -> None:
        super().quiesce_operations()
        self.checkpoint('quiesce')
        self.orphan_writing = False

    def snapshot_plugins(self) -> dict:
        self.call('snapshot')
        snapshot = deepcopy(self.database)
        probe_module().validate_snapshot(snapshot, self.definitions)
        if not self.running:
            assert not self.orphan_writing, 'A same-site one-off can still modify the recovery baseline'
            self.frozen_snapshots.append((self.version, snapshot))
            self.checkpoint('frozen_snapshot')
        return snapshot

    def recovery_snapshot(self) -> dict:
        self.call('snapshot_raw')
        return deepcopy(self.database)

    def restore_plugin_state(self, snapshot: dict) -> None:
        self.call('restore_plugins')
        assert not self.running
        assert not self.orphan_writing
        probe_module().validate_recovery(snapshot, self.database)
        for row in self.database['plugins']:
            row['status'] = 'installed'

    def start_applications(self) -> None:
        super().start_applications()
        self.running = True

    def verify(self) -> None:
        super().verify()
        probe_module().validate_snapshot(self.database, self.definitions)


@pytest.fixture
def site(tmp_path: Path) -> tuple[DatabaseRuntime, object]:
    runtime = DatabaseRuntime()
    manager = manager_module().ReleaseManager(tmp_path / 'site', runtime=runtime)
    manager.deploy(bundle(tmp_path / 'v1', 'v1'))
    runtime.calls.clear()
    runtime.frozen_snapshots.clear()
    return runtime, manager


def change_while_running(runtime: DatabaseRuntime, enabled: str) -> None:
    assert runtime.running, 'The operator must still be able to write through the old application'
    runtime.set_enabled(enabled)


def interrupt() -> None:
    raise KeyboardInterrupt('release manager interrupted')


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_update_uses_enablement_after_old_application_writers_stop(tmp_path: Path, site: tuple, enabled: str) -> None:
    runtime, manager = site
    runtime.set_enabled('1' if enabled == '0' else '0')
    runtime.hooks['infrastructure'] = lambda: change_while_running(runtime, enabled)

    result = manager.deploy(bundle(tmp_path / 'v2', 'v2'))

    assert result == manager.status()
    assert result['current']['version'] == 'v2'
    assert result['pending'] is None
    assert runtime.running
    assert result['current']['snapshot'] == runtime.database
    assert result['previous']['snapshot'] == runtime.database
    assert runtime.frozen_snapshots == [('v1', runtime.database)]


@pytest.mark.parametrize('operation', ['initialize', 'start', 'health'])
def test_failed_update_restores_latest_enablement(tmp_path: Path, site: tuple, operation: str) -> None:
    runtime, manager = site
    runtime.hooks['infrastructure'] = lambda: change_while_running(runtime, '1')
    runtime.fail.add((operation, 'v2'))

    with pytest.raises(manager_module().ReleaseError, match='已恢复上一运行版本'):
        manager.deploy(bundle(tmp_path / 'v2', 'v2'))

    result = manager.status()
    assert runtime.running
    assert result['current']['version'] == 'v1'
    assert result['current']['snapshot'] == runtime.database
    assert result['current']['snapshot']['plugins'][1]['enabled'] == '1'
    assert result['pending'] is None


def test_infrastructure_failure_rebaselines_enablement_during_restore(tmp_path: Path, site: tuple) -> None:
    runtime, manager = site

    def fail_after_operator_change() -> None:
        change_while_running(runtime, '1')
        raise RuntimeError('infrastructure failed before applications stopped')

    runtime.hooks['infrastructure'] = fail_after_operator_change
    with pytest.raises(manager_module().ReleaseError, match='已恢复上一运行版本'):
        manager.deploy(bundle(tmp_path / 'v2', 'v2'))

    result = manager.status()
    assert runtime.running
    assert result['current']['snapshot'] == runtime.database
    assert result['current']['snapshot']['plugins'][1]['enabled'] == '1'
    assert result['pending'] is None


@pytest.mark.parametrize('checkpoint', ['stopped', 'frozen_snapshot', 'before_save', 'after_save'])
def test_interrupted_update_recovers_even_with_a_stale_saved_snapshot(
    tmp_path: Path, site: tuple, monkeypatch: pytest.MonkeyPatch, checkpoint: str
) -> None:
    runtime, manager = site
    runtime.hooks['infrastructure'] = lambda: change_while_running(runtime, '1')
    original_save = manager.save

    def save(state: dict) -> None:
        if state['pending'] and state['pending']['snapshot']['plugins'][1]['enabled'] == '1':
            if checkpoint == 'after_save':
                original_save(state)
            interrupt()
        original_save(state)

    if checkpoint in {'before_save', 'after_save'}:
        monkeypatch.setattr(manager, 'save', save)
    else:
        runtime.hooks[checkpoint] = interrupt
    with pytest.raises(KeyboardInterrupt, match='interrupted'):
        manager.deploy(bundle(tmp_path / 'v2', 'v2'))
    monkeypatch.setattr(manager, 'save', original_save)
    assert not runtime.running
    pending = manager.status()['pending']
    assert pending['kind'] == 'deploy'
    expected_enabled = '1' if checkpoint == 'after_save' else '0'
    assert pending['snapshot']['plugins'][1]['enabled'] == expected_enabled

    # A fresh manager models the documented retry from persisted state only.
    retry = manager_module().ReleaseManager(manager.site, runtime=runtime)
    result = retry.rollback()
    assert runtime.running
    assert result == retry.status()
    assert result['current']['version'] == 'v1'
    assert result['current']['snapshot'] == runtime.database
    assert result['current']['snapshot']['plugins'][1]['enabled'] == '1'
    assert result['previous'] is None
    assert result['pending'] is None


def test_update_quiesces_same_site_oneoffs_before_authoritative_snapshot(tmp_path: Path, site: tuple) -> None:
    runtime, manager = site

    def leave_writer() -> None:
        change_while_running(runtime, '1')
        runtime.orphan_writing = True

    runtime.hooks['infrastructure'] = leave_writer
    result = manager.deploy(bundle(tmp_path / 'v2', 'v2'))
    assert result['current']['snapshot'] == runtime.database
    assert runtime.frozen_snapshots == [('v1', runtime.database)]
    assert not runtime.orphan_writing


@pytest.mark.parametrize('interrupted', [False, True])
def test_manual_rollback_keeps_enable_change_until_applications_stop(
    tmp_path: Path, site: tuple, interrupted: bool
) -> None:
    runtime, manager = site
    manager.deploy(bundle(tmp_path / 'v2', 'v2'))
    runtime.hooks['before_stop'] = lambda: change_while_running(runtime, '1')
    if interrupted:
        runtime.hooks['stopped'] = interrupt
        with pytest.raises(KeyboardInterrupt):
            manager.rollback()
        assert manager.status()['pending']['kind'] == 'rollback'

    result = manager.rollback()
    assert runtime.running
    assert result == manager.status()
    assert result['current']['version'] == 'v1'
    assert result['current']['snapshot'] == runtime.database
    assert result['current']['snapshot']['plugins'][1]['enabled'] == '1'
    assert result['previous']['version'] == 'v2'
    assert result['pending'] is None


@pytest.mark.parametrize(
    'collection,field,value',
    [
        ('plugins', 'version', '2'),
        ('plugins', 'installed_version', '2'),
        ('plugins', 'plugin_id', 'unexpected'),
        ('plugins', 'enabled', 'invalid'),
        ('migrations', 'migration_checksum', 'changed'),
        ('migrations', 'version', '2'),
        ('migrations', 'status', 'failed'),
    ],
)
def test_enablement_rebaseline_keeps_version_migration_and_identity_guards(
    tmp_path: Path, site: tuple, collection: str, field: str, value: str
) -> None:
    runtime, manager = site

    def drift() -> None:
        change_while_running(runtime, '1')
        runtime.database[collection][0][field] = value

    runtime.hooks['infrastructure'] = drift
    with pytest.raises(manager_module().ReleaseError, match='恢复失败'):
        manager.deploy(bundle(tmp_path / 'v2', 'v2'))
    assert not runtime.running
    assert ('initialize', 'v2') not in runtime.calls
    assert manager.status()['pending']['kind'] == 'deploy'
    with pytest.raises(manager_module().ReleaseError, match='恢复失败'):
        manager.rollback()
    assert not runtime.running
    assert manager.status()['pending']['kind'] == 'deploy'
