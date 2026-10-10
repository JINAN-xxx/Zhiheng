import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]


def load_module(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bootstrap() -> object:
    return load_module(ROOT / 'zhiheng-fastapi-backend/scripts/initialize_platform.py')


def database_boundary(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    *,
    databases: dict[str, str],
    marker: str | None = None,
    table_count: int = 2,
) -> tuple[list[tuple[str, tuple]], MagicMock]:
    """Emulate only PostgreSQL I/O; execute the real bootstrap decisions."""
    admin = MagicMock()
    application = MagicMock()
    statements = []
    result = {}

    def execute(query: object, parameters: tuple = ()) -> None:
        text = str(query)
        statements.append((text, parameters))
        if 'pg_database' in text:
            owner = databases.get(parameters[0])
            result['row'] = (owner,) if owner else None
        elif 'pg_roles' in text:
            result['row'] = (1,)
        elif 'to_regclass' in text:
            result['row'] = (marker if marker and marker in text + repr(parameters) else None,)
        elif 'marker_id' in text and 'SELECT' in text:
            result['row'] = (1,)
        elif 'pg_catalog.pg_tables' in text:
            result['row'] = (table_count,)

    for connection in (admin, application):
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = execute
        cursor.fetchone.side_effect = lambda: result.get('row')
    connect = MagicMock(side_effect=[admin, application])
    monkeypatch.setattr(module.psycopg2, 'connect', connect)
    return statements, connect


def test_default_database_identity_uses_zhiheng(bootstrap: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('DB_DATABASE', raising=False)
    monkeypatch.delenv('DB_USERNAME', raising=False)
    monkeypatch.setenv('DB_PASSWORD', 'test-only')
    settings = bootstrap.database_settings()
    assert (settings['dbname'], settings['user']) == ('zhiheng-fastapi', 'zhiheng')


@pytest.mark.parametrize('marker', ['public.ruoyi_bootstrap_state', 'public.zhiheng_bootstrap_state'])
def test_initialized_database_with_either_marker_never_reimports_sql(
    bootstrap: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, marker: str
) -> None:
    statements, connect = database_boundary(monkeypatch, bootstrap, databases={'ruoyi-fastapi': 'ruoyi'}, marker=marker)
    settings = {'dbname': 'ruoyi-fastapi', 'user': 'ruoyi', 'password': 'test-only'}
    bootstrap.initialize_database(settings, 'admin-test', tmp_path / 'must-not-be-read.sql')
    assert connect.call_args_list[1].kwargs == settings
    assert not any('CREATE' in query or 'INSERT' in query for query, _ in statements)


def test_unmarked_nonempty_database_is_still_rejected(
    bootstrap: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    statements, _ = database_boundary(monkeypatch, bootstrap, databases={'ruoyi-fastapi': 'ruoyi'})
    with pytest.raises(bootstrap.BootstrapError, match='数据库非空'):
        bootstrap.initialize_database(
            {'dbname': 'ruoyi-fastapi', 'user': 'ruoyi', 'password': 'test-only'},
            'admin-test',
            tmp_path / 'must-not-be-read.sql',
        )
    assert not any('CREATE' in query or 'INSERT' in query for query, _ in statements)


@pytest.mark.parametrize(
    ('target', 'other'), [('ruoyi-fastapi', 'zhiheng-fastapi'), ('zhiheng-fastapi', 'ruoyi-fastapi')]
)
def test_existing_alternative_database_prevents_silent_empty_install(
    bootstrap: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str, other: str
) -> None:
    statements, connect = database_boundary(monkeypatch, bootstrap, databases={other: 'existing-owner'}, table_count=0)
    baseline = tmp_path / 'baseline.sql'
    baseline.write_text('CREATE TABLE should_not_be_created (id integer);')
    with pytest.raises(bootstrap.BootstrapError, match='PLATFORM_DB_NAME'):
        bootstrap.initialize_database(
            {'dbname': target, 'user': 'ruoyi', 'password': 'test-only'},
            'admin-test',
            baseline,
        )
    assert connect.call_count == 1
    assert not any('CREATE' in query for query, _ in statements)


def test_compose_uses_zhiheng_services_and_preserves_data_volumes() -> None:
    compose = yaml.safe_load((ROOT / 'compose.yaml').read_text())
    services = compose['services']
    assert {'zhiheng-backend-pg', 'zhiheng-frontend', 'zhiheng-redis'} <= services.keys()
    assert not {'ruoyi-backend-pg', 'ruoyi-frontend', 'ruoyi-redis'} & services.keys()
    assert services['zhiheng-backend-pg']['environment']['DB_DATABASE'] == '${PLATFORM_DB_NAME:-zhiheng-fastapi}'
    assert services['zhiheng-backend-pg']['environment']['DB_USERNAME'] == '${PLATFORM_DB_USER:-zhiheng}'
    assert 'postgres-data:/var/lib/postgresql/data' in services['litellm-db']['volumes']
    assert 'platform-redis-data:/data' in services['zhiheng-redis']['volumes']
    assert 'http://zhiheng-backend-pg:9099' in (ROOT / 'zhiheng-fastapi-frontend/bin/nginx.dockerpg.conf').read_text()


def test_update_stops_existing_service_names_before_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module(ROOT / 'deploy/platform.py')
    path = tmp_path / '.env'
    path.write_text(f'PLATFORM_DATA_DIR={tmp_path / "data"}\n')
    module.initialize_environment(path)
    calls = []
    monkeypatch.setattr(module, 'run', lambda command, **kwargs: calls.append(command) or '')
    module.deploy('update', path, build=False)
    stop_index = next(i for i, command in enumerate(calls) if 'stop' in command)
    init_index = next(i for i, command in enumerate(calls) if 'rm' in command)
    assert calls[stop_index][-3:] == ['stop', 'zhiheng-frontend', 'zhiheng-backend-pg']
    assert stop_index < init_index
    assert not any('--volumes' in command or '-v' in command for command in calls)


@pytest.mark.parametrize('action', ['init', 'start', 'update', 'restart'])
def test_legacy_service_names_stop_deployment_before_container_or_data_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    module = load_module(ROOT / 'deploy/platform.py')
    path = tmp_path / '.env'
    data = tmp_path / 'data'
    path.write_text(f'PLATFORM_DATA_DIR={data}\n')
    module.initialize_environment(path)
    calls = []

    def run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        return 'ruoyi-redis\n' if command[:2] == ['docker', 'ps'] else ''

    monkeypatch.setattr(module, 'run', run)
    with pytest.raises(module.DeploymentError, match='旧命名'):
        module.deploy(action, path, build=False)
    inspection = next(command for command in calls if command[:2] == ['docker', 'ps'])
    assert '--all' in inspection
    assert 'label=com.docker.compose.project=zhiheng' in inspection
    assert not any(set(command) & {'up', 'build', 'stop', 'rm'} for command in calls)
    assert not data.exists()


def test_split_compose_uses_zhiheng_names_and_external_litellm() -> None:
    compose = yaml.safe_load((ROOT / 'docker-compose.pg.yml').read_text())
    services = compose['services']
    assert set(services) == {'zhiheng-frontend', 'zhiheng-backend-pg', 'zhiheng-redis'}
    assert compose['networks']['litellm-network']['external'] is True
    assert services['zhiheng-backend-pg']['environment']['DB_DATABASE'] == '${PLATFORM_DB_NAME:-zhiheng-fastapi}'
    assert services['zhiheng-backend-pg']['build']['context'] == './zhiheng-fastapi-backend'
    assert './zhiheng-fastapi-frontend:/frontend:ro' in services['zhiheng-backend-pg']['volumes']


def test_legacy_backend_redis_target_exists_in_its_compose_network() -> None:
    compose = yaml.safe_load((ROOT / 'docker-compose.pg.yml').read_text())
    services = compose['services']
    backend = services['zhiheng-backend-pg']
    environment = dict(dotenv_values(ROOT / 'zhiheng-fastapi-backend/.env.dockerpg'))
    environment.update(backend['environment'])
    redis_host = environment['REDIS_HOST']
    assert redis_host in services, '旧部署继承的 Redis 地址必须是实际存在的服务'
    assert set(backend['networks']) & set(services[redis_host]['networks'])


def test_split_runner_refuses_old_service_names_before_starting_replacements(tmp_path: Path) -> None:
    fake_docker = tmp_path / 'docker'
    capture = tmp_path / 'docker-calls'
    fake_docker.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$FAKE_DOCKER_CAPTURE"\n'
        'if [ "$1" = ps ]; then printf "ruoyi-backend-pg\\n"; fi\n'
    )
    fake_docker.chmod(0o755)
    path = tmp_path / '.env'
    path.write_text('ZHIHENG_DB_PASSWORD=test-only\n')
    result = subprocess.run(
        [str(ROOT / 'deploy/legacy/manage.sh'), 'update'],
        env={
            **os.environ,
            'DOCKER_COMMAND': str(fake_docker),
            'ZHIHENG_ENV_FILE': str(path),
            'ZHIHENG_COMPOSE_PROJECT': 'existing-project',
            'FAKE_DOCKER_CAPTURE': str(capture),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert '旧命名' in result.stderr
    calls = capture.read_text()
    assert 'label=com.docker.compose.project=existing-project' in calls
    assert 'up -d' not in calls
