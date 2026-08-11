import subprocess
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_ROOT = REPOSITORY_ROOT / 'deploy' / 'litellm'


def load_yaml(name: str) -> dict[str, object]:
    with (DEPLOYMENT_ROOT / name).open(encoding='utf-8') as file:
        return yaml.safe_load(file)


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
    assert target.stat().st_mode & 0o777 == 0o600
    assert 'LITELLM_DB_PASSWORD=' in first_content
    assert 'LITELLM_REDIS_PASSWORD=' in first_content
    assert 'LITELLM_MASTER_KEY=sk-' in first_content
    assert 'LITELLM_SALT_KEY=sk-' in first_content
