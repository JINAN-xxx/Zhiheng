from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_zhiheng_redis_requires_authentication_and_stays_local_only() -> None:
    compose = (REPOSITORY_ROOT / 'compose.yaml').read_text(encoding='utf-8')
    assert 'nginx:latest' in compose
    assert 'ZHIHENG_REDIS_PASSWORD' in compose
    assert '--requirepass' in compose
    assert 'REDISCLI_AUTH' in compose
    assert 'REDIS_PASSWORD: ${ZHIHENG_REDIS_PASSWORD' in compose


def test_zhiheng_bootstrap_persists_a_generated_redis_password() -> None:
    script = (REPOSITORY_ROOT / 'deploy' / 'zhiheng' / 'init-shared-postgres.sh').read_text(encoding='utf-8')
    assert 'ZHIHENG_REDIS_PASSWORD' in script
    assert 'openssl rand -hex 24' in script
