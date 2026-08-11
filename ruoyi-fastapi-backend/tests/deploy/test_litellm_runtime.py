import os
import subprocess
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_ROOT = REPOSITORY_ROOT / 'deploy' / 'litellm'
FILE_MODE_MASK = 0o777
PRIVATE_FILE_MODE = 0o600


def load_yaml(name: str) -> dict[str, object]:
    with (DEPLOYMENT_ROOT / name).open(encoding='utf-8') as file:
        return yaml.safe_load(file)


def load_repository_yaml(name: str) -> dict[str, object]:
    with (REPOSITORY_ROOT / name).open(encoding='utf-8') as file:
        return yaml.safe_load(file)


def read_environment_value(path: Path, key: str) -> str:
    """读取仓库环境模板中的简单 KEY = VALUE 配置。"""
    for line in path.read_text(encoding='utf-8').splitlines():
        name, separator, value = line.partition('=')
        if separator and name.strip() == key:
            return value.strip().strip('\'"')
    raise AssertionError(f'{path} 缺少 {key}')


def test_litellm_compose_uses_pinned_local_images_and_private_data_services() -> None:
    compose = load_yaml('compose.yaml')
    services = compose['services']

    assert set(services) == {'litellm-proxy', 'litellm-db', 'litellm-redis'}
    assert {name: service['container_name'] for name, service in services.items()} == {
        'litellm-proxy': 'litellm-proxy',
        'litellm-db': 'litellm-db',
        'litellm-redis': 'litellm-redis',
    }
    assert services['litellm-proxy']['image'] == '${LITELLM_IMAGE:-ghcr.io/berriai/litellm-database:v1.95.0}'
    assert services['litellm-db']['image'].endswith('postgres:16.14-alpine3.24}')
    assert services['litellm-redis']['image'].endswith('redis:7.4.7}')
    assert services['litellm-proxy']['ports'] == ['127.0.0.1:${LITELLM_PORT:-14000}:4000']
    assert 'ports' not in services['litellm-db']
    assert 'ports' not in services['litellm-redis']


def test_litellm_compose_waits_for_stateful_dependencies() -> None:
    services = load_yaml('compose.yaml')['services']

    assert services['litellm-proxy']['depends_on'] == {
        'litellm-db': {'condition': 'service_healthy'},
        'litellm-redis': {'condition': 'service_healthy'},
    }
    assert services['litellm-db']['healthcheck']
    assert services['litellm-redis']['healthcheck']
    assert services['litellm-proxy']['healthcheck']


def test_litellm_config_uses_database_redis_and_environment_secrets() -> None:
    config = load_yaml('config.yaml')

    assert config['model_list'] == []
    assert config['general_settings']['store_model_in_db'] is True
    assert config['general_settings']['database_url'] == 'os.environ/DATABASE_URL'
    assert config['general_settings']['master_key'] == 'os.environ/LITELLM_MASTER_KEY'
    assert config['router_settings']['redis_password'] == 'os.environ/LITELLM_REDIS_PASSWORD'
    assert config['litellm_settings']['json_logs'] is True


def test_litellm_local_environment_is_ignored() -> None:
    gitignore = (REPOSITORY_ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    env_example = (DEPLOYMENT_ROOT / '.env.example').read_text(encoding='utf-8')

    assert '/deploy/litellm/.env' in gitignore
    for key in (
        'LITELLM_DB_PASSWORD',
        'LITELLM_REDIS_PASSWORD',
        'LITELLM_MASTER_KEY',
        'LITELLM_SALT_KEY',
    ):
        assert f'{key}=' in env_example


def test_init_env_generates_private_idempotent_secrets(tmp_path: Path) -> None:
    target = tmp_path / '.env'
    script = DEPLOYMENT_ROOT / 'init-env.sh'

    subprocess.run([str(script), str(target)], check=True)
    first_content = target.read_text(encoding='utf-8')
    subprocess.run([str(script), str(target)], check=True)

    assert target.read_text(encoding='utf-8') == first_content
    assert target.stat().st_mode & FILE_MODE_MASK == PRIVATE_FILE_MODE
    assert 'LITELLM_DB_PASSWORD=' in first_content
    assert 'LITELLM_REDIS_PASSWORD=' in first_content
    assert 'LITELLM_MASTER_KEY=sk-' in first_content
    assert 'LITELLM_SALT_KEY=sk-' in first_content


def test_ruoyi_backend_joins_external_litellm_network_with_runtime_only_credentials() -> None:
    """RuoYi Backend 通过外部网络访问 Proxy，且不持有 Master Key。"""
    compose = load_repository_yaml('docker-compose.pg.yml')
    services = compose['services']
    backend = services['ruoyi-backend-pg']

    assert set(backend['networks']) == {'ruoyi-network', 'litellm-network'}
    assert compose['networks']['litellm-network'] == {'external': True, 'name': 'litellm-network'}
    assert backend['environment']['LITELLM_BASE_URL'] == 'http://litellm-proxy:4000'
    assert backend['environment']['LITELLM_RUNTIME_KEY'].startswith('${LITELLM_RUNTIME_KEY:')
    assert 'LITELLM_MASTER_KEY' not in backend['environment']
    assert services['ruoyi-redis']['networks'] == ['ruoyi-network']


def test_ruoyi_compose_reuses_litellm_postgres_with_separate_credentials() -> None:
    """RuoYi 复用 PostgreSQL 实例，但只持有自己的数据库凭据。"""
    compose = load_repository_yaml('docker-compose.pg.yml')
    services = compose['services']
    backend = services['ruoyi-backend-pg']

    assert set(services) == {'ruoyi-frontend', 'ruoyi-backend-pg', 'ruoyi-redis'}
    assert backend['depends_on'] == {'ruoyi-redis': {'condition': 'service_healthy'}}
    assert backend['environment']['DB_HOST'] == 'litellm-db'
    assert backend['environment']['DB_PORT'] == '5432'
    assert backend['environment']['DB_USERNAME'] == 'ruoyi'
    assert backend['environment']['DB_DATABASE'] == 'ruoyi-fastapi'
    assert backend['environment']['DB_PASSWORD'].startswith('${RUOYI_DB_PASSWORD:')
    assert 'LITELLM_DB_PASSWORD' not in backend['environment']
    assert 'LITELLM_MASTER_KEY' not in backend['environment']


def test_shared_postgres_initializer_has_non_destructive_guard() -> None:
    """已有 RuoYi 表时，初始化器不得重复导入破坏性基线 SQL。"""
    script = (REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh').read_text(encoding='utf-8')

    assert "to_regclass('public.sys_user')" in script
    assert 'ruoyi-fastapi-backend/sql/ruoyi-fastapi-pg.sql' in script
    assert 'RUOYI_DB_PASSWORD' in script
    assert 'LITELLM_DB_PASSWORD' not in script


def test_platform_environment_templates_contain_no_runtime_secret() -> None:
    """受控环境模板只提供非敏感 LiteLLM 配置。"""
    backend_root = REPOSITORY_ROOT / 'ruoyi-fastapi-backend'
    expected_base_urls = {
        '.env.dev': 'http://127.0.0.1:14000',
        '.env.prod': 'http://127.0.0.1:14000',
        '.env.dockerpg': 'http://litellm-proxy:4000',
    }

    for name, expected_base_url in expected_base_urls.items():
        environment_path = backend_root / name
        assert read_environment_value(environment_path, 'LITELLM_BASE_URL') == expected_base_url
        assert read_environment_value(environment_path, 'LITELLM_RUNTIME_KEY') == ''
        assert read_environment_value(environment_path, 'LITELLM_CONNECT_TIMEOUT_SECONDS') == '5'
        assert read_environment_value(environment_path, 'LITELLM_REQUEST_TIMEOUT_SECONDS') == '600'
        assert read_environment_value(environment_path, 'LITELLM_VERIFY_SSL') == 'true'


def test_runtime_key_script_is_private_idempotent_and_never_prints_key(tmp_path: Path) -> None:
    """Runtime Key 只生成一次、安全落盘，且不出现在命令输出中。"""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_curl = fake_bin / 'curl'
    capture_path = tmp_path / 'curl-arguments'
    fake_curl.write_text(
        """#!/bin/sh
set -eu
printf '%s\n' CALL "$@" >> "$FAKE_CURL_CAPTURE"
printf '%s\n' '{"key":"sk-runtime-test"}'
""",
        encoding='utf-8',
    )
    fake_curl.chmod(0o755)

    deploy_environment = tmp_path / 'litellm.env'
    deploy_environment.write_text(
        'LITELLM_MASTER_KEY=sk-master-test\nLITELLM_PORT=14000\n',
        encoding='utf-8',
    )
    platform_environment = tmp_path / '.env'
    platform_environment.write_text('EXISTING_SETTING=kept\nLITELLM_RUNTIME_KEY=\n', encoding='utf-8')
    script = DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'
    environment = {
        **os.environ,
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_CURL_CAPTURE': str(capture_path),
        'LITELLM_DEPLOY_ENV_FILE': str(deploy_environment),
    }

    first = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    first_content = platform_environment.read_text(encoding='utf-8')
    second = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert platform_environment.read_text(encoding='utf-8') == first_content
    assert platform_environment.stat().st_mode & FILE_MODE_MASK == PRIVATE_FILE_MODE
    assert 'EXISTING_SETTING=kept' in first_content
    assert 'LITELLM_RUNTIME_KEY=sk-runtime-test' in first_content
    assert capture_path.read_text(encoding='utf-8').count('CALL') == 1
    captured_arguments = capture_path.read_text(encoding='utf-8')
    assert 'Authorization: Bearer sk-master-test' in captured_arguments
    assert '{"key_alias":"platform-runtime","key_type":"llm_api","models":[]}' in captured_arguments
    combined_output = first.stdout + first.stderr + second.stdout + second.stderr
    assert 'sk-runtime-test' not in combined_output
    assert 'sk-master-test' not in combined_output
