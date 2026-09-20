import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
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


def make_shared_postgres_test_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """创建可观察 stdin、但不接触真实容器的命令环境。"""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    capture_path = tmp_path / 'docker-capture'
    fake_docker = fake_bin / 'docker'
    fake_docker.write_text(
        """#!/bin/sh
set -eu
printf 'CALL\n' >> "$FAKE_DOCKER_CAPTURE"
for argument in "$@"; do
    printf '[%s]\n' "$argument" >> "$FAKE_DOCKER_CAPTURE"
done
if [ "${1:-}" = 'inspect' ]; then
    printf 'true\n'
    exit 0
fi
case "$*" in
    *to_regclass*ruoyi_bootstrap_state*)
        if [ -n "${FAKE_BOOTSTRAP_STATE:-}" ]; then
            printf '%s\n' 'public.ruoyi_bootstrap_state'
        fi
        exit 0
        ;;
    *'from public.ruoyi_bootstrap_state'*)
        printf '%s\n' "${FAKE_BOOTSTRAP_STATE:-}"
        exit 0
        ;;
    *pg_catalog.pg_tables*)
        printf '%s\n' "${FAKE_PUBLIC_TABLE_COUNT:-0}"
        exit 0
        ;;
esac
case " $* " in
    *' exec -i '*)
        printf 'STDIN\n' >> "$FAKE_DOCKER_CAPTURE"
        cat >> "$FAKE_DOCKER_CAPTURE"
        case " $* " in
            *--single-transaction*)
                [ "${FAKE_IMPORT_FAIL:-0}" != '1' ] || exit 1
                ;;
        esac
        ;;
esac
""",
        encoding='utf-8',
    )
    fake_docker.chmod(0o755)
    fake_openssl = fake_bin / 'openssl'
    fake_openssl.write_text(
        """#!/bin/sh
set -eu
case "${3:-}" in
    24) printf '%s\n' '0123456789abcdef0123456789abcdef0123456789abcdef' ;;
    32) printf '%s\n' '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' ;;
    *) exit 1 ;;
esac
""",
        encoding='utf-8',
    )
    fake_openssl.chmod(0o755)
    environment = {
        **os.environ,
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_DOCKER_CAPTURE': str(capture_path),
    }
    return environment, capture_path


def make_runtime_key_test_environment(
    tmp_path: Path,
    *,
    response_key: str = 'sk-runtime-test',
) -> tuple[dict[str, str], Path, Path]:
    """创建不会访问真实 LiteLLM 的 Runtime Key 命令环境。"""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    capture_path = tmp_path / 'curl-arguments'
    response_path = tmp_path / 'curl-response.json'
    response_path.write_text(json.dumps({'key': response_key}), encoding='utf-8')
    fake_curl = fake_bin / 'curl'
    fake_curl.write_text(
        """#!/bin/sh
set -eu
printf '%s\n' CALL "$@" >> "$FAKE_CURL_CAPTURE"
cat "$FAKE_CURL_RESPONSE"
""",
        encoding='utf-8',
    )
    fake_curl.chmod(0o755)
    deploy_environment = tmp_path / 'litellm.env'
    environment = {
        **os.environ,
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_CURL_CAPTURE': str(capture_path),
        'FAKE_CURL_RESPONSE': str(response_path),
        'LITELLM_DEPLOY_ENV_FILE': str(deploy_environment),
    }
    return environment, capture_path, deploy_environment


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
        'LITELLM_MANAGEMENT_KEY',
    ):
        assert f'{key}=' in env_example

    assert read_environment_value(DEPLOYMENT_ROOT / '.env.example', 'LITELLM_MANAGEMENT_KEY') == ''


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


def test_ruoyi_backend_joins_external_litellm_network_with_dual_control_credentials() -> None:
    """Backend 仅持有职责分离的 Runtime 和 Management Key。"""
    compose = load_repository_yaml('docker-compose.pg.yml')
    services = compose['services']
    backend = services['ruoyi-backend-pg']
    frontend = services['ruoyi-frontend']

    assert set(backend['networks']) == {'ruoyi-network', 'litellm-network'}
    assert compose['networks']['litellm-network'] == {'external': True, 'name': 'litellm-network'}
    assert backend['environment']['LITELLM_BASE_URL'] == 'http://litellm-proxy:4000'
    assert backend['environment']['LITELLM_RUNTIME_KEY'].startswith('${LITELLM_RUNTIME_KEY:')
    assert backend['environment']['LITELLM_MANAGEMENT_KEY'] == (
        '${LITELLM_MANAGEMENT_KEY:?set LITELLM_MANAGEMENT_KEY in repository .env}'
    )
    assert not {
        'LITELLM_RUNTIME_KEY',
        'LITELLM_MANAGEMENT_KEY',
        'LITELLM_MASTER_KEY',
    } & set(frontend.get('environment', {}))
    assert not {
        'LITELLM_MASTER_KEY',
        'LITELLM_DB_PASSWORD',
        'LITELLM_REDIS_PASSWORD',
    } & set(backend['environment'])
    assert services['ruoyi-redis']['networks'] == ['ruoyi-network']


def test_ruoyi_backend_requires_persistent_jwt_secret() -> None:
    """容器重建必须复用平台环境中的 JWT 签名密钥。"""
    backend = load_repository_yaml('docker-compose.pg.yml')['services']['ruoyi-backend-pg']

    assert backend['environment']['JWT_SECRET_KEY'] == (
        '${JWT_SECRET_KEY:?run deploy/ruoyi/init-shared-postgres.sh first}'
    )


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


def test_ruoyi_compose_restarts_services_and_waits_for_backend_health() -> None:
    """Docker 重启后平台自动恢复，前端只在后端可用后启动。"""
    services = load_repository_yaml('docker-compose.pg.yml')['services']

    assert {service['restart'] for service in services.values()} == {'unless-stopped'}
    assert services['ruoyi-backend-pg']['healthcheck']
    assert services['ruoyi-frontend']['depends_on'] == {'ruoyi-backend-pg': {'condition': 'service_healthy'}}


def test_ruoyi_backend_mounts_frontend_plugin_sources_read_only() -> None:
    """插件启动校验可读取前端源码，但后端不能修改宿主机文件。"""
    compose = load_repository_yaml('docker-compose.pg.yml')
    backend = compose['services']['ruoyi-backend-pg']

    assert './ruoyi-fastapi-frontend:/frontend:ro' in backend['volumes']


def test_shared_postgres_initializer_has_non_destructive_guard() -> None:
    """已有 RuoYi 表时，初始化器不得重复导入破坏性基线 SQL。"""
    script = (REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh').read_text(encoding='utf-8')

    assert 'ruoyi_bootstrap_state' in script
    assert 'WHERE marker_id = 1' in script
    assert '--single-transaction' in script
    assert "to_regclass('public.sys_user')" not in script
    assert 'ruoyi-fastapi-backend/sql/ruoyi-fastapi-pg.sql' in script
    assert 'RUOYI_DB_PASSWORD' in script
    assert 'LITELLM_DB_PASSWORD' not in script


def test_shared_postgres_initializer_generates_private_password_without_leaking(tmp_path: Path) -> None:
    """初始化器保留平台配置、私密落盘密码，且不向日志输出密码。"""
    environment, capture_path = make_shared_postgres_test_environment(tmp_path)
    platform_environment = tmp_path / '.env'
    platform_environment.write_text('LITELLM_RUNTIME_KEY=sk-runtime-test\n', encoding='utf-8')
    script = REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh'

    first = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    first_content = platform_environment.read_text(encoding='utf-8')
    environment['FAKE_BOOTSTRAP_STATE'] = 'complete'
    second = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    password = '0123456789abcdef0123456789abcdef0123456789abcdef'
    jwt_secret = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
    assert platform_environment.read_text(encoding='utf-8') == first_content
    assert platform_environment.stat().st_mode & FILE_MODE_MASK == PRIVATE_FILE_MODE
    assert 'LITELLM_RUNTIME_KEY=sk-runtime-test' in first_content
    assert f'RUOYI_DB_PASSWORD={password}' in first_content
    assert f'JWT_SECRET_KEY={jwt_secret}' in first_content
    command_output = first.stdout + first.stderr + second.stdout + second.stderr
    assert password not in command_output
    assert jwt_secret not in command_output
    assert capture_path.read_text(encoding='utf-8').count('drop table if exists sys_dept;') == 1


def test_shared_postgres_initializer_serializes_concurrent_secret_generation(tmp_path: Path) -> None:
    """两个初始化进程并发启动时只能生成一组平台密钥。"""
    environment, _ = make_shared_postgres_test_environment(tmp_path)
    environment['FAKE_BOOTSTRAP_STATE'] = 'complete'
    platform_environment = tmp_path / '.env'
    platform_environment.write_text('LITELLM_RUNTIME_KEY=sk-runtime-test\n', encoding='utf-8')
    openssl_calls = tmp_path / 'openssl-calls'
    fake_openssl = Path(environment['PATH'].split(os.pathsep, 1)[0]) / 'openssl'
    fake_openssl.write_text(
        """#!/bin/sh
set -eu
printf 'call\n' >> "$FAKE_OPENSSL_CALLS"
sleep 0.2
case "${3:-}" in
    24) printf '%s\n' '0123456789abcdef0123456789abcdef0123456789abcdef' ;;
    32) printf '%s\n' '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' ;;
    *) exit 1 ;;
esac
""",
        encoding='utf-8',
    )
    fake_openssl.chmod(0o755)
    environment['FAKE_OPENSSL_CALLS'] = str(openssl_calls)
    script = REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh'

    processes = [
        subprocess.Popen(
            [str(script), str(platform_environment)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert openssl_calls.read_text(encoding='utf-8').splitlines() == ['call', 'call', 'call']
    content = platform_environment.read_text(encoding='utf-8')
    assert content.count('RUOYI_DB_PASSWORD=') == 1
    assert content.count('JWT_SECRET_KEY=') == 1
    assert content.count('RUOYI_REDIS_PASSWORD=') == 1


def test_shared_postgres_initializer_skips_seed_when_bootstrap_marker_exists(tmp_path: Path) -> None:
    """检测到专用完成标记后，仅同步角色密码，不重复导入基线数据。"""
    environment, capture_path = make_shared_postgres_test_environment(tmp_path)
    environment['FAKE_BOOTSTRAP_STATE'] = 'complete'
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'RUOYI_DB_PASSWORD=abcdef0123456789abcdef0123456789abcdef0123456789\nLITELLM_RUNTIME_KEY=sk-runtime-test\n',
        encoding='utf-8',
    )
    script = REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh'

    subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    captured = capture_path.read_text(encoding='utf-8')
    assert 'ruoyi_bootstrap_state' in captured
    assert 'drop table if exists sys_dept;' not in captured


def test_shared_postgres_initializer_fails_atomically_without_reporting_ready(tmp_path: Path) -> None:
    """基线导入失败时使用单事务回滚，且不得误报数据库 ready。"""
    environment, capture_path = make_shared_postgres_test_environment(tmp_path)
    environment['FAKE_IMPORT_FAIL'] = '1'
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'RUOYI_DB_PASSWORD=abcdef0123456789abcdef0123456789abcdef0123456789\n',
        encoding='utf-8',
    )
    script = REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh'

    result = subprocess.run(
        [str(script), str(platform_environment)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    captured = capture_path.read_text(encoding='utf-8')
    assert result.returncode != 0
    assert '--single-transaction' in captured
    assert 'ruoyi_bootstrap_state' in captured
    assert 'RuoYi shared PostgreSQL is ready.' not in result.stdout


def test_shared_postgres_initializer_refuses_unknown_existing_schema(tmp_path: Path) -> None:
    """无完成标记但已有表时拒绝执行含 DROP TABLE 的基线。"""
    environment, capture_path = make_shared_postgres_test_environment(tmp_path)
    environment['FAKE_PUBLIC_TABLE_COUNT'] = '1'
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'RUOYI_DB_PASSWORD=abcdef0123456789abcdef0123456789abcdef0123456789\n',
        encoding='utf-8',
    )
    script = REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh'

    result = subprocess.run(
        [str(script), str(platform_environment)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert 'without a completed bootstrap marker' in result.stderr
    assert 'drop table if exists sys_dept;' not in capture_path.read_text(encoding='utf-8')


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
        assert read_environment_value(environment_path, 'LITELLM_MANAGEMENT_KEY') == ''
        assert read_environment_value(environment_path, 'LITELLM_CONNECT_TIMEOUT_SECONDS') == '5'
        assert read_environment_value(environment_path, 'LITELLM_REQUEST_TIMEOUT_SECONDS') == '600'
        assert read_environment_value(environment_path, 'LITELLM_VERIFY_SSL') == 'true'


def test_dockerpg_environment_targets_shared_postgres_without_committed_password() -> None:
    """镜像内模板与 Compose 使用同一数据库地址，但不固化运行密码。"""
    environment_path = REPOSITORY_ROOT / 'ruoyi-fastapi-backend/.env.dockerpg'

    assert read_environment_value(environment_path, 'DB_HOST') == 'litellm-db'
    assert read_environment_value(environment_path, 'DB_PORT') == '5432'
    assert read_environment_value(environment_path, 'DB_USERNAME') == 'ruoyi'
    assert read_environment_value(environment_path, 'DB_PASSWORD') == ''
    assert read_environment_value(environment_path, 'DB_DATABASE') == 'ruoyi-fastapi'


def test_postgres_image_build_tolerates_slow_package_mirror() -> None:
    """首次镜像构建允许国内 PyPI 镜像出现短暂读取停顿。"""
    dockerfile = (REPOSITORY_ROOT / 'ruoyi-fastapi-backend/Dockerfile.pg').read_text(encoding='utf-8')

    assert '--timeout 60' in dockerfile
    assert '--retries 3' in dockerfile
    assert 'https://pypi.org/simple' in dockerfile
    assert '--mount=type=cache,target=/root/.cache/pip' in dockerfile


def test_postgres_image_includes_default_ai_plugin_python_dependencies() -> None:
    """后端镜像重建后仍满足默认启用 AI 插件的 Python 依赖。"""
    manifest = load_repository_yaml('ruoyi-fastapi-backend/plugins/ai/plugin.yaml')
    requirements = {
        line.strip()
        for line in (REPOSITORY_ROOT / 'ruoyi-fastapi-backend/requirements-pg.txt')
        .read_text(encoding='utf-8')
        .splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    }

    assert set(manifest['dependencies']['python']) <= requirements


def test_frontend_image_uses_the_committed_build_without_requiring_node() -> None:
    """部署镜像直接使用已发布前端，不在服务器重复执行 Node 构建。"""
    dockerfile = (REPOSITORY_ROOT / 'ruoyi-fastapi-frontend/Dockerfile').read_text(encoding='utf-8')

    assert 'COPY dist .' in dockerfile
    assert 'npm ' not in dockerfile


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
        'LITELLM_MASTER_KEY=sk-master-test\nLITELLM_MANAGEMENT_KEY=sk-management-test\nLITELLM_PORT=14000\n',
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
    assert read_environment_value(platform_environment, 'LITELLM_RUNTIME_KEY') == 'sk-runtime-test'
    assert read_environment_value(platform_environment, 'LITELLM_MANAGEMENT_KEY') == 'sk-management-test'
    assert capture_path.read_text(encoding='utf-8').count('CALL') == 1
    captured_arguments = capture_path.read_text(encoding='utf-8')
    assert 'Authorization: Bearer sk-master-test' in captured_arguments
    assert '{"key_alias":"platform-runtime","key_type":"llm_api","models":[]}' in captured_arguments
    combined_output = first.stdout + first.stderr + second.stdout + second.stderr
    assert 'sk-runtime-test' not in combined_output
    assert 'sk-master-test' not in combined_output
    assert 'sk-management-test' not in combined_output


def test_runtime_key_script_preserves_existing_management_key_and_skips_generation(
    tmp_path: Path,
) -> None:
    """Platform 已有双 Key 时不调用 LiteLLM，也不被环境覆盖。"""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_curl = fake_bin / 'curl'
    capture_path = tmp_path / 'curl-arguments'
    fake_curl.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' CALLED >> "$FAKE_CURL_CAPTURE"\nexit 99\n',
        encoding='utf-8',
    )
    fake_curl.chmod(0o755)
    python_capture_path = tmp_path / 'python-called'
    for python_name in ('python3', 'python'):
        fake_python = fake_bin / python_name
        fake_python.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' CALLED >> "$FAKE_PYTHON_CAPTURE"\nexit 99\n',
            encoding='utf-8',
        )
        fake_python.chmod(0o755)
    deploy_environment = tmp_path / 'litellm.env'
    deploy_environment.write_text('LITELLM_MANAGEMENT_KEY=sk-deploy-management-test\n', encoding='utf-8')
    platform_environment = tmp_path / '.env'
    original_content = (
        'EXISTING_SETTING=kept\n'
        'LITELLM_RUNTIME_KEY=sk-existing-runtime\n'
        'LITELLM_MANAGEMENT_KEY=sk-existing-management\n'
    )
    platform_environment.write_text(original_content, encoding='utf-8')
    script = DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'
    environment = {
        **os.environ,
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_CURL_CAPTURE': str(capture_path),
        'FAKE_PYTHON_CAPTURE': str(python_capture_path),
        'LITELLM_DEPLOY_ENV_FILE': str(deploy_environment),
        'LITELLM_MANAGEMENT_KEY': 'sk-process-management-test',
    }

    result = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    rewritten_content = platform_environment.read_text(encoding='utf-8')
    assert 'EXISTING_SETTING=kept' in rewritten_content
    assert read_environment_value(platform_environment, 'LITELLM_RUNTIME_KEY') == 'sk-existing-runtime'
    assert read_environment_value(platform_environment, 'LITELLM_MANAGEMENT_KEY') == 'sk-existing-management'
    assert platform_environment.stat().st_mode & FILE_MODE_MASK == PRIVATE_FILE_MODE
    assert not capture_path.exists()
    assert not python_capture_path.exists()
    combined_output = result.stdout + result.stderr
    for secret in (
        'sk-deploy-management-test',
        'sk-process-management-test',
        'sk-existing-runtime',
        'sk-existing-management',
    ):
        assert secret not in combined_output


def test_runtime_key_script_syncs_management_without_copying_master(tmp_path: Path) -> None:
    """Runtime 已有时仍同步 Management，缺失时保持空值。"""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_curl = fake_bin / 'curl'
    capture_path = tmp_path / 'curl-arguments'
    fake_curl.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' CALLED >> "$FAKE_CURL_CAPTURE"\nexit 99\n',
        encoding='utf-8',
    )
    fake_curl.chmod(0o755)
    deploy_environment = tmp_path / 'litellm.env'
    deploy_environment.write_text('LITELLM_MASTER_KEY=sk-master-test\n', encoding='utf-8')
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'EXISTING_SETTING=kept\nLITELLM_RUNTIME_KEY=sk-existing-runtime\n',
        encoding='utf-8',
    )
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

    assert 'EXISTING_SETTING=kept' in first_content
    assert read_environment_value(platform_environment, 'LITELLM_RUNTIME_KEY') == 'sk-existing-runtime'
    assert read_environment_value(platform_environment, 'LITELLM_MANAGEMENT_KEY') == ''
    assert platform_environment.read_text(encoding='utf-8') == first_content
    assert not capture_path.exists()
    combined_output = first.stdout + first.stderr + second.stdout + second.stderr
    assert 'sk-existing-runtime' not in combined_output
    assert 'sk-master-test' not in combined_output


def test_runtime_key_script_syncs_explicit_management_when_runtime_exists(tmp_path: Path) -> None:
    """已有 Runtime 时跳过生成，但继续同步进程显式提供的 Management。"""
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_curl = fake_bin / 'curl'
    capture_path = tmp_path / 'curl-arguments'
    fake_curl.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' CALLED >> "$FAKE_CURL_CAPTURE"\nexit 99\n',
        encoding='utf-8',
    )
    fake_curl.chmod(0o755)
    deploy_environment = tmp_path / 'litellm.env'
    deploy_environment.write_text('LITELLM_MASTER_KEY=sk-master-test\n', encoding='utf-8')
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'EXISTING_SETTING=kept\nLITELLM_RUNTIME_KEY=sk-existing-runtime\n',
        encoding='utf-8',
    )
    script = DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'
    environment = {
        **os.environ,
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_CURL_CAPTURE': str(capture_path),
        'LITELLM_DEPLOY_ENV_FILE': str(deploy_environment),
        'LITELLM_MANAGEMENT_KEY': 'sk-explicit-management',
    }

    result = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    content = platform_environment.read_text(encoding='utf-8')
    assert 'EXISTING_SETTING=kept' in content
    assert read_environment_value(platform_environment, 'LITELLM_RUNTIME_KEY') == 'sk-existing-runtime'
    assert read_environment_value(platform_environment, 'LITELLM_MANAGEMENT_KEY') == 'sk-explicit-management'
    assert not capture_path.exists()
    combined_output = result.stdout + result.stderr
    assert 'sk-existing-runtime' not in combined_output
    assert 'sk-explicit-management' not in combined_output
    assert 'sk-master-test' not in combined_output


def test_runtime_key_script_never_executes_deployment_environment(tmp_path: Path) -> None:
    """部署 dotenv 中的 Shell 元字符只能作为数据处理或被安全拒绝。"""
    environment, capture_path, deploy_environment = make_runtime_key_test_environment(tmp_path)
    marker = tmp_path / 'dotenv-command-was-executed'
    deploy_environment.write_text(
        f'LITELLM_MASTER_KEY=sk-master-test\nLITELLM_MANAGEMENT_KEY=sk-management-test; touch {marker}\n',
        encoding='utf-8',
    )
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'LITELLM_RUNTIME_KEY=sk-existing-runtime\n',
        encoding='utf-8',
    )

    result = subprocess.run(
        [str(DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'), str(platform_environment)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert not capture_path.exists()
    assert platform_environment.read_text(encoding='utf-8') == ('LITELLM_RUNTIME_KEY=sk-existing-runtime\n')


def test_runtime_key_script_writes_compose_lossless_special_characters(tmp_path: Path) -> None:
    """写入的双 Key 对 Compose 保真，不发生变量插值或注释截断。"""
    if shutil.which('docker') is None:
        pytest.skip('docker compose is required for the dotenv compatibility check')

    runtime_key = 'sk-runtime-$DOLLAR # hash and space'
    management_key = 'sk-management-$DOLLAR # hash and space'
    environment, _capture_path, deploy_environment = make_runtime_key_test_environment(
        tmp_path,
        response_key=runtime_key,
    )
    environment['LITELLM_MANAGEMENT_KEY'] = management_key
    deploy_environment.write_text(
        'LITELLM_MASTER_KEY=sk-master-test\nLITELLM_PORT=14000\n',
        encoding='utf-8',
    )
    platform_environment = tmp_path / 'platform.env'
    platform_environment.write_text('EXISTING_SETTING=kept\n', encoding='utf-8')

    subprocess.run(
        [str(DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    compose_file = tmp_path / 'compose.yaml'
    compose_file.write_text(
        """services:
  probe:
    image: scratch
    environment:
      PROBE_RUNTIME: "${LITELLM_RUNTIME_KEY:?missing runtime}"
      PROBE_MANAGEMENT: "${LITELLM_MANAGEMENT_KEY:?missing management}"
""",
        encoding='utf-8',
    )
    compose_environment = {
        key: value for key, value in os.environ.items() if key not in {'LITELLM_RUNTIME_KEY', 'LITELLM_MANAGEMENT_KEY'}
    }
    compose_command = [
        'docker',
        'compose',
        '--env-file',
        str(platform_environment),
        '-f',
        str(compose_file),
        'config',
    ]
    environment_result = subprocess.run(
        [*compose_command, '--environment'],
        check=True,
        capture_output=True,
        text=True,
        env=compose_environment,
        cwd=tmp_path,
    )
    parsed_environment = {}
    for line in environment_result.stdout.splitlines():
        name, separator, value = line.partition('=')
        if separator and name in {'LITELLM_RUNTIME_KEY', 'LITELLM_MANAGEMENT_KEY'}:
            parsed_environment[name] = value

    assert parsed_environment == {
        'LITELLM_RUNTIME_KEY': runtime_key,
        'LITELLM_MANAGEMENT_KEY': management_key,
    }

    compose_result = subprocess.run(
        [*compose_command, '--format', 'json'],
        check=True,
        capture_output=True,
        text=True,
        env=compose_environment,
        cwd=tmp_path,
    )
    compose = json.loads(compose_result.stdout)
    probe_environment = compose['services']['probe']['environment']

    assert probe_environment['PROBE_RUNTIME'] == runtime_key.replace('$', '$$')
    assert probe_environment['PROBE_MANAGEMENT'] == management_key.replace('$', '$$')


def test_runtime_key_script_rejects_malformed_platform_env_before_remote_call(
    tmp_path: Path,
) -> None:
    """现有 Platform dotenv 无法可靠解析时不得创建远端 Runtime Key。"""
    environment, capture_path, deploy_environment = make_runtime_key_test_environment(tmp_path)
    deploy_environment.write_text('LITELLM_MASTER_KEY=sk-master-test\n', encoding='utf-8')
    platform_environment = tmp_path / '.env'
    original_content = "EXISTING_SETTING=kept\nLITELLM_RUNTIME_KEY='unterminated\n"
    platform_environment.write_text(original_content, encoding='utf-8')

    result = subprocess.run(
        [str(DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'), str(platform_environment)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert not capture_path.exists()
    assert platform_environment.read_text(encoding='utf-8') == original_content


def test_runtime_key_script_stages_local_write_before_remote_call(tmp_path: Path) -> None:
    """目标目录不可 staging 时不得先创建远端 Runtime Key。"""
    environment, capture_path, deploy_environment = make_runtime_key_test_environment(tmp_path)
    deploy_environment.write_text('LITELLM_MASTER_KEY=sk-master-test\n', encoding='utf-8')
    platform_environment = tmp_path / 'missing-parent' / '.env'

    result = subprocess.run(
        [str(DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'), str(platform_environment)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert not capture_path.exists()
    assert not platform_environment.exists()


def test_runtime_key_script_keeps_original_when_final_replace_fails(tmp_path: Path) -> None:
    """远端生成后的最终替换失败时，原文件保持不变且清理临时凭据。"""
    environment, capture_path, deploy_environment = make_runtime_key_test_environment(tmp_path)
    deploy_environment.write_text(
        'LITELLM_MASTER_KEY=sk-master-test\nLITELLM_MANAGEMENT_KEY=sk-management-test\n',
        encoding='utf-8',
    )
    fake_mv = tmp_path / 'bin' / 'mv'
    fake_mv.write_text('#!/bin/sh\nexit 73\n', encoding='utf-8')
    fake_mv.chmod(0o755)
    platform_environment = tmp_path / '.env'
    original_content = 'EXISTING_SETTING=kept\nLITELLM_RUNTIME_KEY=\n'
    platform_environment.write_text(original_content, encoding='utf-8')

    result = subprocess.run(
        [str(DEPLOYMENT_ROOT / 'init-platform-runtime-key.sh'), str(platform_environment)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert capture_path.read_text(encoding='utf-8').count('CALL') == 1
    assert platform_environment.read_text(encoding='utf-8') == original_content
    assert not list(tmp_path.glob('.env.base.*'))
    assert not list(tmp_path.glob('.env.tmp.*'))
    combined_output = result.stdout + result.stderr
    for secret in ('sk-runtime-test', 'sk-master-test', 'sk-management-test'):
        assert secret not in combined_output
