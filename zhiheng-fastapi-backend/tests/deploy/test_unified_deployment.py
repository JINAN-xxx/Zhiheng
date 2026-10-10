import fcntl
import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

from plugins.ai.knowledge.graph_tokenizer import GRAPH_TOKENIZER_CACHE_KEY, GRAPH_TOKENIZER_SHA256

ROOT = Path(__file__).resolve().parents[3]
PRIVATE_FILE_MODE = 0o600
MINIMUM_SECRET_LENGTH = 32


def deployment_module() -> object:
    path = ROOT / 'deploy/platform.py'
    assert path.is_file(), '统一部署入口需要实际可执行的编排模块'
    spec = importlib.util.spec_from_file_location('platform_deployment', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unified_compose_is_self_contained_and_gates_backend_on_initialization() -> None:
    path = ROOT / 'compose.yaml'
    assert path.is_file(), '需要一个包含完整依赖的主 Compose'
    compose = yaml.safe_load(path.read_text())
    services = compose['services']
    assert set(services) == {
        'litellm-db',
        'litellm-redis',
        'litellm-proxy',
        'zhiheng-redis',
        'milvus',
        'platform-init',
        'zhiheng-backend-pg',
        'zhiheng-frontend',
    }
    assert (
        services['zhiheng-backend-pg']['depends_on']['platform-init']['condition'] == 'service_completed_successfully'
    )
    assert all(not network.get('external') for network in compose.get('networks', {}).values())
    assert all('container_name' not in service for service in services.values())
    assert all(
        'ports' not in services[name]
        for name in ('litellm-db', 'zhiheng-redis', 'litellm-redis', 'milvus', 'litellm-proxy')
    )
    volumes = str(services['zhiheng-backend-pg']['volumes'])
    assert '/app/data/knowledge' in volumes and '/app/data/agent-skills' in volumes and '/app/vf_admin' in volumes
    assert 'pull_policy: never' not in path.read_text()
    assert 'LITELLM_DB_PASSWORD' not in services['zhiheng-backend-pg']['environment']


def test_environment_generation_is_private_and_stable_across_repeated_runs(tmp_path: Path) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    first = module.initialize_environment(path)
    second = module.initialize_environment(path)
    assert first == second
    assert (path.stat().st_mode & 0o777) == PRIVATE_FILE_MODE
    assert first['LITELLM_MANAGEMENT_KEY'] == first['LITELLM_MASTER_KEY']
    assert first['LITELLM_RUNTIME_KEY'] != first['LITELLM_MASTER_KEY']
    for key in (
        'LITELLM_SALT_KEY',
        'JWT_SECRET_KEY',
        'ZHIHENG_DB_PASSWORD',
        'ZHIHENG_REDIS_PASSWORD',
        'AI_API_KEY_PEPPER_V1',
    ):
        assert len(first[key]) >= MINIMUM_SECRET_LENGTH
    assert first['PLATFORM_PORT'] == '12580'


def test_legacy_secret_names_are_migrated_without_rotating_values(tmp_path: Path) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    original = module.initialize_environment(path)
    legacy = (
        path.read_text()
        .replace('ZHIHENG_DB_PASSWORD', 'RUOYI_DB_PASSWORD')
        .replace('ZHIHENG_REDIS_PASSWORD', 'RUOYI_REDIS_PASSWORD')
    )
    path.write_text(legacy)
    # Read-only commands must receive canonical values without rewriting the file.
    assert module.read_environment(path)['ZHIHENG_DB_PASSWORD'] == original['ZHIHENG_DB_PASSWORD']
    assert path.read_text() == legacy
    migrated = module.initialize_environment(path)
    assert migrated == original
    assert 'RUOYI_DB_PASSWORD' not in path.read_text()
    assert 'RUOYI_REDIS_PASSWORD' not in path.read_text()


def test_conflicting_legacy_and_canonical_secrets_are_rejected_without_rewriting(tmp_path: Path) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    content = 'ZHIHENG_DB_PASSWORD=canonical-private\nRUOYI_DB_PASSWORD=legacy-private\n'
    path.write_text(content)
    with pytest.raises(module.DeploymentError, match='冲突') as caught:
        module.initialize_environment(path)
    assert 'canonical-private' not in str(caught.value)
    assert 'legacy-private' not in str(caught.value)
    assert path.read_text() == content


def test_environment_preserves_existing_keys_and_rejects_symlinks(tmp_path: Path) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    path.write_text('JWT_SECRET_KEY="keep-this-jwt-secret-value-123456789"\nCUSTOM_SETTING=preserved\n')
    result = module.initialize_environment(path)
    assert result['JWT_SECRET_KEY'] == 'keep-this-jwt-secret-value-123456789'
    assert result['CUSTOM_SETTING'] == 'preserved'
    linked = tmp_path / 'link.env'
    linked.symlink_to(path)
    with pytest.raises(module.DeploymentError):
        module.initialize_environment(linked)


def test_existing_legacy_environment_is_not_silently_rekeyed(tmp_path: Path) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    text = 'ZHIHENG_DB_PASSWORD=existing-password\nLITELLM_RUNTIME_KEY=sk-existing\n'
    path.write_text(text)
    with pytest.raises(module.DeploymentError, match='旧部署'):
        module.initialize_environment(path)
    assert path.read_text() == text


@pytest.mark.parametrize(
    'missing_key', ['AI_API_KEY_PEPPER_V1', 'LITELLM_SALT_KEY', 'LITELLM_MANAGEMENT_KEY', 'JWT_SECRET_KEY']
)
def test_initialized_environment_never_recreates_missing_persistent_secrets(tmp_path: Path, missing_key: str) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    module.initialize_environment(path)
    damaged = '\n'.join(line for line in path.read_text().splitlines() if not line.startswith(missing_key + '=')) + '\n'
    path.write_text(damaged)
    with pytest.raises(module.DeploymentError, match=missing_key):
        module.initialize_environment(path)
    assert path.read_text() == damaged


def test_invalid_port_is_rejected_without_rewriting_environment(tmp_path: Path) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    path.write_text('PLATFORM_PORT=70000\n')
    with pytest.raises(module.DeploymentError):
        module.initialize_environment(path)
    assert path.read_text() == 'PLATFORM_PORT=70000\n'


def test_update_build_failure_leaves_existing_application_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    module.initialize_environment(path)
    calls = []

    def run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        if 'build' in command or '--build' in command:
            raise module.DeploymentError('build failed')
        return ''

    monkeypatch.setattr(module, 'run', run)
    with pytest.raises(module.DeploymentError):
        module.deploy('update', path)
    assert not any('stop' in command for command in calls), '镜像构建失败不能提前停掉线上应用'


def test_concurrent_deployment_is_rejected_before_compose_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    module.initialize_environment(path)
    called = []
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: called.append(args) or '')
    with path.with_name(path.name + '.deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(module.DeploymentError, match='正在执行'):
            module.deploy('update', path)
    assert not called


@pytest.mark.parametrize('action', ['logs', 'status'])
def test_read_only_commands_do_not_take_the_deployment_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    module.initialize_environment(path)
    called = []
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: called.append(args) or '')
    with path.with_name(path.name + '.deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        module.deploy(action, path)
    assert called


def test_graph_vocabulary_is_bundled_and_matches_the_runtime_checksum() -> None:

    resource = ROOT / 'zhiheng-fastapi-backend/resources/tokenizer' / GRAPH_TOKENIZER_CACHE_KEY
    assert resource.is_file(), '首次构建不能依赖不稳定的分词资源在线下载'
    assert hashlib.sha256(resource.read_bytes()).hexdigest() == GRAPH_TOKENIZER_SHA256
    dockerfile = (ROOT / 'zhiheng-fastapi-backend/Dockerfile.pg').read_text()
    assert '--source-cache /app/resources/tokenizer' in dockerfile


def test_repeated_init_stops_existing_application_before_running_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = deployment_module()
    path = tmp_path / '.env'
    module.initialize_environment(path)
    calls = []

    def run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        return 'existing-app' if 'ps' in command else ''

    monkeypatch.setattr(module, 'run', run)
    module.deploy('init', path, build=False)
    stop_index = next((i for i, command in enumerate(calls) if 'stop' in command), None)
    up_index = next(i for i, command in enumerate(calls) if 'up' in command)
    assert stop_index is not None and stop_index < up_index
