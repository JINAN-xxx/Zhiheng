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
    *to_regclass*)
        printf '%s\n' "${FAKE_SCHEMA_RESULT:-}"
        exit 0
        ;;
esac
case " $* " in
    *' exec -i '*)
        printf 'STDIN\n' >> "$FAKE_DOCKER_CAPTURE"
        cat >> "$FAKE_DOCKER_CAPTURE"
        ;;
esac
""",
        encoding='utf-8',
    )
    fake_docker.chmod(0o755)
    fake_openssl = fake_bin / 'openssl'
    fake_openssl.write_text(
        "#!/bin/sh\nprintf '%s\n' '0123456789abcdef0123456789abcdef0123456789abcdef'\n",
        encoding='utf-8',
    )
    fake_openssl.chmod(0o755)
    environment = {
        **os.environ,
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_DOCKER_CAPTURE': str(capture_path),
    }
    return environment, capture_path


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


def test_ruoyi_backend_mounts_frontend_plugin_sources_read_only() -> None:
    """插件启动校验可读取前端源码，但后端不能修改宿主机文件。"""
    compose = load_repository_yaml('docker-compose.pg.yml')
    backend = compose['services']['ruoyi-backend-pg']

    assert './ruoyi-fastapi-frontend:/frontend:ro' in backend['volumes']


def test_shared_postgres_initializer_has_non_destructive_guard() -> None:
    """已有 RuoYi 表时，初始化器不得重复导入破坏性基线 SQL。"""
    script = (REPOSITORY_ROOT / 'deploy/ruoyi/init-shared-postgres.sh').read_text(encoding='utf-8')

    assert "to_regclass('public.sys_user')" in script
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
    environment['FAKE_SCHEMA_RESULT'] = 'sys_user'
    second = subprocess.run(
        [str(script), str(platform_environment)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    password = '0123456789abcdef0123456789abcdef0123456789abcdef'
    assert platform_environment.read_text(encoding='utf-8') == first_content
    assert platform_environment.stat().st_mode & FILE_MODE_MASK == PRIVATE_FILE_MODE
    assert 'LITELLM_RUNTIME_KEY=sk-runtime-test' in first_content
    assert f'RUOYI_DB_PASSWORD={password}' in first_content
    assert password not in first.stdout + first.stderr + second.stdout + second.stderr
    assert capture_path.read_text(encoding='utf-8').count('drop table if exists sys_dept;') == 1


def test_shared_postgres_initializer_skips_seed_when_schema_exists(tmp_path: Path) -> None:
    """检测到核心表后，仅同步角色密码，不重复导入基线数据。"""
    environment, capture_path = make_shared_postgres_test_environment(tmp_path)
    environment['FAKE_SCHEMA_RESULT'] = 'sys_user'
    platform_environment = tmp_path / '.env'
    platform_environment.write_text(
        'RUOYI_DB_PASSWORD=abcdef0123456789abcdef0123456789abcdef0123456789\n'
        'LITELLM_RUNTIME_KEY=sk-runtime-test\n',
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
    assert "to_regclass('public.sys_user')" in captured
    assert 'drop table if exists sys_dept;' not in captured


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

    assert '--timeout 120' in dockerfile
    assert '--retries 5' in dockerfile


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


def test_frontend_image_build_has_enough_v8_heap() -> None:
    """完整前端打包不受 Node 默认约 2 GiB 堆上限约束。"""
    dockerfile = (REPOSITORY_ROOT / 'ruoyi-fastapi-frontend/Dockerfile').read_text(encoding='utf-8')

    assert 'NODE_OPTIONS=--max-old-space-size=4096' in dockerfile


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
