import pytest
from cryptography.fernet import Fernet

from zhiheng.cli import main as cli


def test_source_reload_uses_factory_and_preserves_private_request_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ZHIHENG_DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1/test')
    monkeypatch.setenv('ZHIHENG_REDIS_URL', 'redis://127.0.0.1:6379/0')
    monkeypatch.setenv('ZHIHENG_CREDENTIAL_KEY', Fernet.generate_key().decode())
    calls = []

    def capture(application: str, **options: object) -> None:
        calls.append((application, options))

    monkeypatch.setattr(cli.uvicorn, 'run', capture)
    assert cli.main(['serve', '--reload', '--port', '19999']) == 0
    application, options = calls[0]
    assert application == 'zhiheng.api.main:create_configured_app'
    assert options['factory'] is True and options['reload'] is True
    assert options['port'] == 19999 and options['proxy_headers'] is False
    assert options['log_config']['handlers']['access']['filters'] == ['share_tokens']
