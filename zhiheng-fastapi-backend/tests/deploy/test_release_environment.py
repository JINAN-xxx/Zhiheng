import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PRIVATE_FILE_MODE = 0o600
SERVICE_COUNT = 8


def release_environment() -> ModuleType:
    sys.path.insert(0, str(ROOT / 'deploy'))
    assert (ROOT / 'deploy/release/environment.py').is_file(), '发布实例环境管理尚未实现'
    return importlib.import_module('release.environment')


def test_site_keeps_secrets_and_absolute_data_across_releases(tmp_path: Path) -> None:
    module = release_environment()
    site = tmp_path / 'site'
    calls = []

    def generate(command: list[str], **kwargs) -> str:
        calls.append(command)
        return json.dumps({'private': 'PRIVATE\nKEY', 'public': 'PUBLIC\nKEY'})

    first = module.initialize_site(site, 'backend:v1', generate)
    second = module.initialize_site(site, 'backend:v2', generate)
    assert first == second
    assert len(calls) == 1
    assert first['PLATFORM_DATA_DIR'] == str(site / 'data')
    assert first['TRANSPORT_CRYPTO_PRIVATE_KEY'] == 'PRIVATE\\nKEY'
    assert first['PLATFORM_COMPOSE_PROJECT'].startswith('zhiheng-')
    assert site.joinpath('.env').stat().st_mode & 0o777 == PRIVATE_FILE_MODE


def test_partial_transport_keys_never_rotate_existing_identity(tmp_path: Path) -> None:
    module = release_environment()
    site = tmp_path / 'site'
    site.mkdir()
    path = site / '.env'
    original = 'TRANSPORT_CRYPTO_PRIVATE_KEY=existing-key\n'
    path.write_text(original)
    with pytest.raises(Exception, match='密钥'):
        module.initialize_site(site, 'backend:v1', lambda *args, **kwargs: pytest.fail('不得生成替代密钥'))
    assert path.read_text() == original


def test_initialized_site_missing_key_is_rejected(tmp_path: Path) -> None:
    module = release_environment()
    module.initialize_site(tmp_path, 'backend:v1', lambda *a, **kw: json.dumps({'private': 'p', 'public': 'q'}))
    path = tmp_path / '.env'
    path.write_text('\n'.join(line for line in path.read_text().splitlines() if not line.startswith('JWT_SECRET_KEY=')))
    with pytest.raises(Exception, match='JWT_SECRET_KEY'):
        module.initialize_site(tmp_path, 'backend:v2', lambda *a, **kw: pytest.fail('不应生成密钥'))


def test_compose_environment_locks_images_and_ignores_ambient_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = release_environment()
    monkeypatch.setenv('RELEASE_BACKEND_IMAGE', 'unrelated:latest')
    monkeypatch.setenv('PLATFORM_COMPOSE_PROJECT', 'unrelated')
    monkeypatch.setenv('COMPOSE_FILE', '/tmp/unrelated.yml')
    manifest = {'version': 'v1', 'images': {role: {'tag': f'fixed/{role}:v1'} for role in module.IMAGE_ROLES}}
    values = {'PLATFORM_COMPOSE_PROJECT': 'site', 'RELEASE_BACKEND_IMAGE': 'also-unrelated:latest'}
    actual = module.compose_environment(values, manifest, 'identity')
    assert actual['RELEASE_BACKEND_IMAGE'] == 'fixed/backend:v1'
    assert actual['PLATFORM_COMPOSE_PROJECT'] == 'site'
    assert actual['RELEASE_VERSION'] == 'v1'
    assert actual['RELEASE_SITE_ID'] == 'identity'
    assert 'COMPOSE_FILE' not in actual


def test_release_compose_has_only_images_and_persistent_runtime_mounts() -> None:
    path = ROOT / 'deploy/compose.release.yaml'
    assert path.is_file(), '独立镜像部署编排尚未实现'
    services = yaml.safe_load(path.read_text())['services']
    assert len(services) == SERVICE_COUNT
    for service in services.values():
        assert 'build' not in service
        assert service['pull_policy'] == 'never'
        assert service['labels']['io.zhiheng.installation'] == '${RELEASE_SITE_ID:?release manager required}'
        assert 'zhiheng-fastapi-' not in str(service.get('volumes', []))
    assert services['zhiheng-frontend'].get('volumes', []) == []
    backend = services['zhiheng-backend-pg']
    assert backend['environment']['APP_ROOT_PATH'] == '/docker-api'
    assert 'TRANSPORT_CRYPTO_PRIVATE_KEY' in backend['environment']
    assert '/frontend' not in str(backend.get('volumes', []))
    assert set(services['zhiheng-frontend']['depends_on']) == {'zhiheng-backend-pg'}
