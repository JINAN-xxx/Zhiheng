import fcntl
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
ROLES = ('backend', 'frontend', 'postgres', 'redis', 'litellm', 'milvus')


def manager_module() -> ModuleType:
    sys.path.insert(0, str(ROOT / 'deploy'))
    assert (ROOT / 'deploy/release/manager.py').is_file(), '版本部署与回滚尚未实现'
    return importlib.import_module('release.manager')


def bundle(path: Path, version: str, *, contract: str = 'a') -> Path:
    path.mkdir()
    (path / 'images.tar').write_bytes(b'fake Docker archive')
    (path / 'compose.release.yaml').write_text('services: {}\n')
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
    images = {
        role: {
            'tag': f'zhiheng-release/{role}:'
            + (f'{version}-' + 'b' * 12 if role in ('backend', 'frontend') else 'sha-' + '1' * 12),
            'id': 'sha256:' + '1' * 64,
        }
        for role in ROLES
    }
    metadata = {
        'format': 1,
        'version': version,
        'created_at': '2026-09-29T00:00:00Z',
        'source': {'revision': '0' * 40, 'dirty': False, 'sha256': 'b' * 64},
        'platform': 'linux/amd64',
        'images': images,
        'compatibility': {'data_sha256': contract * 64, 'runtime_sha256': 'c' * 64},
        'files': files,
    }
    (path / 'release.json').write_text(json.dumps(metadata))
    return path


class Runtime:
    def __init__(self) -> None:
        self.calls = []
        self.version = None
        self.fail = set()
        self.foreign = False

    def call(self, operation: str) -> None:
        self.calls.append((operation, self.version))
        if (operation, self.version) in self.fail:
            raise RuntimeError('injected failure')

    def run(self, *args, **kwargs) -> str:
        return json.dumps({'private': 'p', 'public': 'q'})

    def load_images(self, path: Path, manifest: dict) -> None:
        self.call('load')

    def configure(self, path: Path, manifest: dict, values: dict, site_id: str) -> None:
        self.version = manifest['version']

    def check_ownership(self) -> None:
        self.call('ownership')
        if self.foreign:
            raise RuntimeError('同项目已有其他实例容器')

    def validate(self) -> None:
        self.call('config')

    def quiesce_operations(self) -> None:
        self.call('quiesce')

    def start_infrastructure(self) -> None:
        self.call('infrastructure')

    def snapshot_plugins(self) -> dict:
        self.call('snapshot')
        return {
            'plugins': [{'plugin_id': 'ai', 'status': 'installed', 'enabled': '0', 'installed_version': '0.9.1'}],
            'migrations': [],
        }

    def recovery_snapshot(self) -> dict:
        return self.snapshot_plugins()

    def stop_applications(self) -> None:
        self.call('stop')

    def initialize(self) -> None:
        self.call('initialize')

    def start_applications(self) -> None:
        self.call('start')

    def verify(self) -> None:
        self.call('health')

    def restore_plugin_state(self, snapshot: dict) -> None:
        assert snapshot
        self.call('restore_plugins')


def setup_site(tmp_path: Path) -> tuple[ModuleType, Runtime, object, Path]:
    module = manager_module()
    runtime = Runtime()
    site = tmp_path / 'site'
    manager = module.ReleaseManager(site, runtime=runtime)
    v1 = bundle(tmp_path / 'bundle1', 'v1')
    manager.deploy(v1)
    return module, runtime, manager, site


def test_successful_update_and_rollback_keep_environment_and_data(tmp_path: Path) -> None:
    _module, runtime, manager, site = setup_site(tmp_path)
    original_env = (site / '.env').read_bytes()
    original_data = site / 'data/knowledge/keep.json'
    original_data.write_text('retained')
    manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    assert manager.status()['current']['version'] == 'v2'
    assert manager.status()['previous']['version'] == 'v1'
    runtime.calls.clear()
    manager.rollback()
    assert manager.status()['current']['version'] == 'v1'
    assert manager.status()['previous']['version'] == 'v2'
    assert manager.status()['pending'] is None
    assert (site / '.env').read_bytes() == original_env
    assert original_data.read_text() == 'retained'
    assert not any(operation in {'initialize', 'infrastructure'} for operation, version in runtime.calls)


@pytest.mark.parametrize('operation', ['initialize', 'start', 'health'])
def test_failed_update_restores_previous_without_claiming_success(tmp_path: Path, operation: str) -> None:
    _module, runtime, manager, _site = setup_site(tmp_path)
    runtime.calls.clear()
    runtime.fail.add((operation, 'v2'))
    with pytest.raises(Exception, match='已恢复'):
        manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    state = manager.status()
    assert state['current']['version'] == 'v1'
    assert state['previous'] is None
    assert state['pending'] is None
    assert ('restore_plugins', 'v1') in runtime.calls
    assert ('initialize', 'v1') not in runtime.calls
    assert ('health', 'v1') in runtime.calls


def test_failed_recovery_preserves_pending_for_manual_rollback(tmp_path: Path) -> None:
    _module, runtime, manager, _site = setup_site(tmp_path)
    runtime.fail.add(('health', 'v2'))
    original_verify = runtime.verify

    def fail_only_during_recovery() -> None:
        if runtime.version == 'v1' and ('health', 'v2') in runtime.calls:
            raise RuntimeError('recovery health failed')
        original_verify()

    runtime.verify = fail_only_during_recovery
    with pytest.raises(Exception, match='恢复失败'):
        manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    assert manager.status()['pending']['to']['version'] == 'v2'
    runtime.fail.clear()
    runtime.verify = original_verify
    manager.rollback()
    assert manager.status()['current']['version'] == 'v1'
    assert manager.status()['pending'] is None


def test_incompatible_update_is_rejected_before_loading_or_stopping(tmp_path: Path) -> None:
    _module, runtime, manager, _site = setup_site(tmp_path)
    runtime.calls.clear()
    with pytest.raises(Exception, match='兼容'):
        manager.deploy(bundle(tmp_path / 'bundle2', 'v2', contract='d'))
    assert runtime.calls == []
    assert manager.status()['current']['version'] == 'v1'


def test_foreign_containers_are_not_stopped(tmp_path: Path) -> None:
    module = manager_module()
    runtime = Runtime()
    runtime.foreign = True
    manager = module.ReleaseManager(tmp_path / 'site', runtime=runtime)
    with pytest.raises(Exception, match='其他实例'):
        manager.deploy(bundle(tmp_path / 'bundle1', 'v1'))
    assert not any(operation in {'stop', 'initialize', 'infrastructure'} for operation, version in runtime.calls)


def test_first_deploy_failure_can_retry_same_version(tmp_path: Path) -> None:
    module = manager_module()
    runtime = Runtime()
    manager = module.ReleaseManager(tmp_path / 'site', runtime=runtime)
    path = bundle(tmp_path / 'bundle1', 'v1')
    runtime.fail.add(('initialize', 'v1'))
    with pytest.raises(module.ReleaseError):
        manager.deploy(path)
    assert manager.status()['current'] is None
    assert manager.status()['pending']['to']['version'] == 'v1'
    runtime.fail.clear()
    manager.deploy(path)
    assert manager.status()['current']['version'] == 'v1'


def test_missing_saved_environment_is_never_regenerated(tmp_path: Path) -> None:
    _module, runtime, manager, site = setup_site(tmp_path)
    (site / '.env').unlink()
    runtime.calls.clear()
    with pytest.raises(Exception, match='配置'):
        manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    assert not (site / '.env').exists()
    assert runtime.calls == []


def test_reused_version_with_different_bytes_is_rejected(tmp_path: Path) -> None:
    _module, runtime, manager, _site = setup_site(tmp_path)
    path = bundle(tmp_path / 'other', 'v1')
    metadata = json.loads((path / 'release.json').read_text())
    metadata['source']['sha256'] = 'e' * 64
    for role in ('backend', 'frontend'):
        metadata['images'][role]['tag'] = f'zhiheng-release/{role}:v1-' + 'e' * 12
    (path / 'release.json').write_text(json.dumps(metadata))
    runtime.calls.clear()
    with pytest.raises(Exception, match='版本'):
        manager.deploy(path)
    assert runtime.calls == []


def test_parallel_deployment_is_rejected(tmp_path: Path) -> None:
    module = manager_module()
    site = tmp_path / 'site'
    site.mkdir()
    with (site / '.release.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Exception, match='执行'):
            module.ReleaseManager(site, runtime=Runtime()).deploy(bundle(tmp_path / 'bundle1', 'v1'))


def test_interrupted_manual_rollback_retries_its_target(tmp_path: Path) -> None:
    _module, runtime, manager, _site = setup_site(tmp_path)
    manager.deploy(bundle(tmp_path / 'bundle2', 'v2'))
    runtime.fail.add(('health', 'v1'))
    with pytest.raises(Exception, match='恢复失败'):
        manager.rollback()
    assert manager.status()['pending']['kind'] == 'rollback'
    runtime.fail.clear()
    manager.rollback()
    assert manager.status()['current']['version'] == 'v1'
    assert manager.status()['previous']['version'] == 'v2'
