from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_ruoyi_redis_requires_authentication_and_stays_local_only() -> None:
    compose = (REPOSITORY_ROOT / 'docker-compose.pg.yml').read_text(encoding='utf-8')
    assert 'image: nginx:latest' in compose or 'image: ruoyi-frontend:latest' in compose
    assert 'RUOYI_REDIS_PASSWORD' in compose
    assert '--requirepass' in compose
    assert 'REDISCLI_AUTH' in compose
    assert '127.0.0.1:16379:6379' in compose
    assert 'REDIS_PASSWORD: "${RUOYI_REDIS_PASSWORD' in compose


def test_ruoyi_bootstrap_persists_a_generated_redis_password() -> None:
    script = (REPOSITORY_ROOT / 'deploy' / 'ruoyi' / 'init-shared-postgres.sh').read_text(encoding='utf-8')
    assert 'RUOYI_REDIS_PASSWORD' in script
    assert 'openssl rand -hex 24' in script
