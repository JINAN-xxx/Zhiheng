import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
ROLES = ('backend', 'frontend', 'postgres', 'redis', 'litellm', 'milvus')


def release_module(name: str) -> ModuleType:
    assert (ROOT / f'deploy/release/{name}.py').is_file(), f'缺少发布模块 {name}'
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(f'deploy.release.{name}')


def metadata() -> dict:
    return {
        'format': 1,
        'version': '1.0.0',
        'created_at': '2026-09-29T00:00:00+00:00',
        'source': {'revision': 'a' * 40, 'dirty': True, 'sha256': 'b' * 64},
        'platform': 'linux/amd64',
        'compatibility': {'data_sha256': 'c' * 64, 'runtime_sha256': 'd' * 64},
        'images': {
            role: {
                'tag': f'zhiheng-release/{role}:'
                + (f'1.0.0-{"b" * 12}' if role in ROLES[:2] else f'sha-{str(i) * 12}'),
                'id': 'sha256:' + str(i) * 64,
            }
            for i, role in enumerate(ROLES)
        },
    }


def minimal_bundle(path: Path) -> Path:
    path.mkdir()
    (path / 'images.tar').write_bytes(b'image archive')
    (path / 'compose.release.yaml').write_text('services: {}\n')
    (path / 'release.sh').write_text('#!/bin/sh\n')
    return path


def test_manifest_roundtrip_detects_tamper(tmp_path: Path) -> None:
    module = release_module('manifest')
    bundle = minimal_bundle(tmp_path / 'bundle')
    written = module.write_manifest(bundle, metadata())
    assert written == module.load_manifest(bundle)
    assert written['files']['images.tar'] == hashlib.sha256(b'image archive').hexdigest()
    assert 'release.json' not in written['files']
    (bundle / 'images.tar').write_bytes(b'changed')
    with pytest.raises(module.ReleaseError, match=r'(?i)(hash|sha256|校验)'):
        module.load_manifest(bundle)
    assert module.load_manifest(bundle, verify_files=False) == written


@pytest.mark.parametrize(
    'unsafe', ['../outside', '/etc/passwd', 'a/../../outside', './images.tar', 'a//b', 'a\\b', 'release.json', 'a\x00b']
)
def test_manifest_rejects_unsafe_paths_without_hash_verification(tmp_path: Path, unsafe: str) -> None:
    module = release_module('manifest')
    bundle = minimal_bundle(tmp_path / 'bundle')
    manifest = module.write_manifest(bundle, metadata())
    manifest['files'][unsafe] = 'e' * 64
    (bundle / 'release.json').write_text(json.dumps(manifest))
    with pytest.raises(module.ReleaseError):
        module.load_manifest(bundle, verify_files=False)


@pytest.mark.parametrize('version', ['', '..', '../escape', 'x/y', 'x:y', 'x y', 'x' * 65])
def test_manifest_rejects_unsafe_versions(tmp_path: Path, version: str) -> None:
    module = release_module('manifest')
    value = metadata()
    value['version'] = version
    with pytest.raises(module.ReleaseError):
        module.write_manifest(minimal_bundle(tmp_path / 'bundle'), value)


def test_manifest_rejects_symlinked_files_and_parents(tmp_path: Path) -> None:
    module = release_module('manifest')
    bundle = minimal_bundle(tmp_path / 'bundle')
    module.write_manifest(bundle, metadata())
    external = tmp_path / 'external'
    external.write_bytes(b'image archive')
    (bundle / 'images.tar').unlink()
    (bundle / 'images.tar').symlink_to(external)
    with pytest.raises(module.ReleaseError, match=r'(?i)(symlink|符号链接)'):
        module.load_manifest(bundle, verify_files=False)
    with pytest.raises(module.ReleaseError):
        module.write_manifest(bundle, metadata())


def test_manifest_rejects_symlinked_parent_and_manifest(tmp_path: Path) -> None:
    module = release_module('manifest')
    bundle = minimal_bundle(tmp_path / 'bundle')
    write_source(bundle, 'nested/file.txt', 'runtime')
    module.write_manifest(bundle, metadata())
    (bundle / 'nested').rename(tmp_path / 'external')
    (bundle / 'nested').symlink_to(tmp_path / 'external', target_is_directory=True)
    with pytest.raises(module.ReleaseError, match=r'(?i)symlink'):
        module.load_manifest(bundle, verify_files=False)
    (bundle / 'nested').unlink()
    (tmp_path / 'external').rename(bundle / 'nested')
    (bundle / 'release.json').rename(tmp_path / 'manifest.json')
    (bundle / 'release.json').symlink_to(tmp_path / 'manifest.json')
    with pytest.raises(module.ReleaseError, match=r'(?i)symlink'):
        module.load_manifest(bundle)


@pytest.mark.parametrize(
    'field,value',
    [
        ('format', True),
        ('format', 2),
        ('platform', 'linux/arm64'),
        ('created_at', 'yesterday'),
        ('source', {}),
        ('compatibility', {}),
        ('images', {}),
        ('extra', 'unexpected'),
    ],
)
def test_manifest_rejects_invalid_schema(tmp_path: Path, field: str, value: object) -> None:
    module = release_module('manifest')
    bundle = minimal_bundle(tmp_path / 'bundle')
    manifest = module.write_manifest(bundle, metadata())
    manifest[field] = value
    (bundle / 'release.json').write_text(json.dumps(manifest))
    with pytest.raises(module.ReleaseError):
        module.load_manifest(bundle)


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    module = release_module('manifest')
    bundle = minimal_bundle(tmp_path / 'bundle')
    module.write_manifest(bundle, metadata())
    path = bundle / 'release.json'
    path.write_text(path.read_text().replace('"format": 1', '"format": 2, "format": 1'))
    with pytest.raises(module.ReleaseError, match='Duplicate'):
        module.load_manifest(bundle)


def write_source(path: Path, name: str, content: str = 'fixture\n') -> None:
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / 'source'
    path.mkdir()
    for name in (
        'deploy/platform.py',
        'deploy/release_cli.py',
        'deploy/release/__init__.py',
        'deploy/release/manifest.py',
        'deploy/release/build.py',
        'deploy/release/environment.py',
        'deploy/release/manager.py',
        'deploy/release/data-version',
        'deploy/release.sh',
        'deploy/litellm/config.yaml',
        'deploy/milvus/embedEtcd.yaml',
        'deploy/milvus/user.yaml',
        'deploy/compose.release.yaml',
        'zhiheng-fastapi-backend/app.py',
        'zhiheng-fastapi-backend/requirements-pg.txt',
        'zhiheng-fastapi-backend/sql/platform.sql',
        'zhiheng-fastapi-backend/scripts/initialize_platform.py',
        'zhiheng-fastapi-backend/plugins/ai/plugin.yaml',
        'zhiheng-fastapi-backend/plugins/ai/migrations/postgresql/001_init.sql',
        'zhiheng-fastapi-backend/plugins/ai/seeds/postgresql/seed.sql',
        'zhiheng-fastapi-frontend/package.json',
        'zhiheng-fastapi-frontend/src/main.js',
        'zhiheng-fastapi-frontend/plugins/ai/plugin.yaml',
        'zhiheng-fastapi-frontend/bin/nginx.dockerpg.conf',
    ):
        write_source(path, name)
    write_source(
        path, 'zhiheng-fastapi-backend/Dockerfile.pg', 'FROM python:3.10\nCOPY . .\nCMD ["python", "app.py"]\n'
    )
    write_source(
        path,
        'deploy/release/Dockerfile.frontend',
        'ARG FRONTEND_BASE_IMAGE\nFROM ${FRONTEND_BASE_IMAGE}\nCOPY dist /usr/share/nginx/html\nCOPY nginx.conf /etc/nginx/conf.d/default.conf\n',
    )
    write_source(
        path,
        'compose.yaml',
        'image: ${PLATFORM_FRONTEND_IMAGE:-nginx:latest}\nimage: ${LITELLM_POSTGRES_IMAGE:-postgres:16}\nimage: ${REDIS_IMAGE:-redis:7}\nimage: ${LITELLM_IMAGE:-litellm:v1}\nimage: ${MILVUS_IMAGE:-milvus:v2}',
    )
    (path / 'zhiheng-fastapi-frontend/node_modules').mkdir()
    write_source(path, 'zhiheng-fastapi-frontend/dist/keep.txt', 'live dist must survive')
    for name in (
        '.env',
        '.env.dockerpg',
        'transport.pem',
        'rsa.key',
        'data/private.json',
        '__pycache__/cache.pyc',
        'logs/run.log',
        '.venv/lib/private.py',
    ):
        write_source(path, f'zhiheng-fastapi-backend/{name}', 'PRIVATE')
    write_source(path, 'zhiheng-fastapi-frontend/.env.local', 'VITE_SECRET=PRIVATE')
    return path


class FakeBuildTools:
    def __init__(self) -> None:
        self.calls = []
        self.images = {
            name: {'Id': 'sha256:' + str(i) * 64, 'Os': 'linux', 'Architecture': 'amd64'}
            for i, name in enumerate(('nginx:latest', 'postgres:16', 'redis:7', 'litellm:v1', 'milvus:v2'), 1)
        }
        self.contexts = []
        self.npm_paths = []
        self.images.update({value['Id']: value for value in list(self.images.values())})

    def __call__(
        self, args: list[str], *, cwd: Path | None = None, check: bool = True, **kwargs
    ) -> subprocess.CompletedProcess:
        args = [str(arg) for arg in args]
        self.calls.append(args)
        output = ''
        code = 0
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            output = 'a' * 40
        elif args[:2] == ['git', 'status']:
            output = ' M changed.py\n'
        elif args[:3] == ['npm', 'run', 'build:docker']:
            path = Path(cwd)
            assert not (path / 'dist').exists()
            assert not (path / '.env.local').exists()
            assert '/docker-api' in (path / '.env.docker').read_text()
            self.npm_paths.append(path)
            write_source(path, 'dist/index.html', 'fresh frontend')
        elif args[:3] == ['docker', 'image', 'inspect']:
            found = self.images.get(args[-1])
            output, code = (json.dumps([found]), 0) if found else ('No such image', 1)
        elif args[:2] == ['docker', 'build']:
            context = Path(args[-1])
            self.contexts.append(
                {
                    str(p.relative_to(context)): p.read_bytes()
                    for p in context.rglob('*')
                    if p.is_file() and not p.is_symlink()
                }
            )
            iid = f'sha256:{len(self.contexts) + 5:064x}'
            Path(args[args.index('--iidfile') + 1]).write_text(iid)
            self.images[iid] = {'Id': iid, 'Os': 'linux', 'Architecture': 'amd64'}
        elif args[:2] == ['docker', 'tag']:
            self.images[args[3]] = self.images[args[2]]
        elif args[:3] == ['docker', 'image', 'save']:
            Path(args[args.index('--output') + 1]).write_bytes(b'all six images')
        else:
            raise AssertionError(f'Unexpected command: {args}')
        result = subprocess.CompletedProcess(args, code, output, '')
        if check and code:
            raise subprocess.CalledProcessError(code, args, output=output)
        return result


def build_with_fake(
    monkeypatch: pytest.MonkeyPatch, source: Path, target: Path
) -> tuple[ModuleType, FakeBuildTools, dict]:
    module = release_module('build')
    fake = FakeBuildTools()
    monkeypatch.setattr(module, 'run', fake)
    return module, fake, module.build_release(source, target, '1.0.0')


def test_build_produces_verified_offline_bundle_without_touching_live_dist(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / 'release'
    _module, fake, manifest = build_with_fake(monkeypatch, source, target)
    assert release_module('manifest').load_manifest(target) == manifest
    assert set(manifest['images']) == set(ROLES)
    assert all(manifest['images'][role]['tag'].startswith(f'zhiheng-release/{role}:1.0.0-') for role in ROLES[:2])
    assert all(v['id'].startswith('sha256:') for v in manifest['images'].values())
    assert (source / 'zhiheng-fastapi-frontend/dist/keep.txt').read_text() == 'live dist must survive'
    assert fake.npm_paths and all(path != source / 'zhiheng-fastapi-frontend' for path in fake.npm_paths)
    assert not fake.npm_paths[0].exists()
    files = manifest['files']
    assert {
        'release.sh',
        'images.tar',
        'compose.release.yaml',
        'deploy/release/manifest.py',
        'deploy/release/environment.py',
    } <= set(files)
    assert not any('.env' in name or 'zhiheng-fastapi-backend' in name or 'node_modules' in name for name in files)
    assert (target / 'release.sh').stat().st_mode & 0o111
    saved = next(call for call in fake.calls if call[:3] == ['docker', 'image', 'save'])
    assert set(saved[-6:]) == {item['tag'] for item in manifest['images'].values()}
    contexts = fake.contexts
    assert len(contexts) == len(ROLES[:2])
    assert all(b'PRIVATE' not in content for context in contexts for content in context.values())
    assert any('plugins/ai/plugin.yaml' in name and 'frontend' in name for name in contexts[0])
    assert any(content == b'fresh frontend' for content in contexts[1].values())
    assert all('--platform' in call and 'linux/amd64' in call for call in fake.calls if call[:2] == ['docker', 'build'])


def test_build_refuses_existing_output_before_running_tools(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = release_module('build')
    target = tmp_path / 'release'
    target.mkdir()
    fake = FakeBuildTools()
    monkeypatch.setattr(module, 'run', fake)
    with pytest.raises(release_module('manifest').ReleaseError):
        module.build_release(source, target, '1.0.0')
    assert fake.calls == []


def test_build_requires_local_node_modules_and_cleans_failed_output(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = release_module('build')
    (source / 'zhiheng-fastapi-frontend/node_modules').rmdir()
    fake = FakeBuildTools()
    monkeypatch.setattr(module, 'run', fake)
    with pytest.raises(release_module('manifest').ReleaseError, match='node_modules'):
        module.build_release(source, tmp_path / 'release', '1.0.0')
    assert not (tmp_path / 'release').exists()


def test_build_refuses_overwriting_different_release_image(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, fake, first = build_with_fake(monkeypatch, source, tmp_path / 'release')
    backend = first['images']['backend']['tag']
    foreign = {'Id': 'sha256:' + 'f' * 64, 'Os': 'linux', 'Architecture': 'amd64'}
    fake.images[backend] = foreign
    with pytest.raises(release_module('manifest').ReleaseError, match=r'(?i)(already|exist|覆盖|冲突)'):
        module.build_release(source, tmp_path / 'release2', '1.0.0')
    assert fake.images[backend] == foreign
    assert not (tmp_path / 'release2').exists()


def test_compatibility_hash_tracks_data_and_runtime_contracts(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, fake, first = build_with_fake(monkeypatch, source, tmp_path / 'release')
    fake = FakeBuildTools()
    monkeypatch.setattr(module, 'run', fake)
    write_source(source, 'zhiheng-fastapi-backend/app.py', 'new application code')
    second = module.build_release(source, tmp_path / 'release2', '1.0.1')
    assert first['source']['sha256'] != second['source']['sha256']
    assert first['compatibility'] == second['compatibility']
    assert {role: first['images'][role]['tag'] for role in ROLES[2:]} == {
        role: second['images'][role]['tag'] for role in ROLES[2:]
    }
    write_source(source, 'deploy/release/data-version', '2\n')
    third = module.build_release(source, tmp_path / 'release3', '1.0.2')
    assert third['compatibility']['data_sha256'] != second['compatibility']['data_sha256']
    assert third['compatibility']['runtime_sha256'] == second['compatibility']['runtime_sha256']
    write_source(source, 'deploy/release/environment.py', 'changed environment contract')
    fourth = module.build_release(source, tmp_path / 'release4', '1.0.3')
    assert fourth['compatibility']['runtime_sha256'] != third['compatibility']['runtime_sha256']


def test_build_rejects_foreign_architecture_before_publishing(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = release_module('build')
    fake = FakeBuildTools()
    fake.images['redis:7']['Architecture'] = 'arm64'
    monkeypatch.setattr(module, 'run', fake)
    with pytest.raises(release_module('manifest').ReleaseError, match=r'(?i)(platform|amd64|架构)'):
        module.build_release(source, tmp_path / 'release', '1.0.0')
    assert not (tmp_path / 'release').exists()


@pytest.mark.parametrize(
    'contract',
    [
        'zhiheng-fastapi-backend/sql/platform.sql',
        'zhiheng-fastapi-backend/plugins/ai/plugin.yaml',
        'zhiheng-fastapi-backend/plugins/ai/migrations/postgresql/001_init.sql',
        'zhiheng-fastapi-backend/plugins/ai/seeds/postgresql/seed.sql',
        'zhiheng-fastapi-backend/scripts/initialize_platform.py',
        'deploy/release/data-version',
    ],
)
def test_each_persistent_contract_change_blocks_shortcut_upgrade(source: Path, contract: str) -> None:
    module = release_module('build')
    original = module._data_hash(source, source)
    (source / contract).write_text('changed storage contract')
    assert module._data_hash(source, source) != original


def test_changed_infrastructure_id_blocks_shortcut_upgrade(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, fake, first = build_with_fake(monkeypatch, source, tmp_path / 'release')
    redis = fake.images['redis:7']
    redis['Id'] = 'sha256:' + 'f' * 64
    fake.images[redis['Id']] = redis
    second = module.build_release(source, tmp_path / 'release2', '1.0.1')
    assert first['compatibility']['data_sha256'] == second['compatibility']['data_sha256']
    assert first['compatibility']['runtime_sha256'] != second['compatibility']['runtime_sha256']


def test_build_snapshot_rejects_source_symlinks(tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = release_module('build')
    (source / 'zhiheng-fastapi-backend/link.py').symlink_to(source / 'zhiheng-fastapi-backend/.env')
    fake = FakeBuildTools()
    monkeypatch.setattr(module, 'run', fake)
    with pytest.raises(release_module('manifest').ReleaseError, match=r'(?i)symlink'):
        module.build_release(source, tmp_path / 'release', '1.0.0')
    assert fake.calls == []


def test_default_images_are_read_from_source_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    module = release_module('build')
    monkeypatch.setenv('REDIS_IMAGE', 'attacker/image:untrusted')
    defaults = module._base_images(ROOT / 'compose.yaml')
    assert set(defaults) == set(ROLES[1:])
    assert defaults['redis'] != 'attacker/image:untrusted'


def test_builder_runner_removes_inherited_vite_values(monkeypatch: pytest.MonkeyPatch) -> None:
    module = release_module('build')
    monkeypatch.setenv('VITE_SECRET', 'should never enter browser code')
    result = module.run([sys.executable, '-c', 'import os; print(os.environ.get("VITE_SECRET", "absent"))'])
    assert result.stdout.strip() == 'absent'
