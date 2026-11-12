import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError


def test_importing_api_does_not_load_configuration_or_legacy_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            (
                'import sys; import zhiheng.api.main; '
                "assert not any(n == 'plugins' or n.startswith(('plugins.', 'config.', 'legacy.')) for n in sys.modules)"
            ),
        ],
        env={k: v for k, v in os.environ.items() if not k.startswith('ZHIHENG_')}
        | {'PYTHONPATH': str(Path(__file__).resolve().parents[3] / 'src')},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_settings_require_explicit_new_platform_credentials() -> None:
    from zhiheng.bootstrap.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url='sqlite:///old.db', redis_url='redis://localhost', credential_key='bad')


def test_settings_hide_database_password_and_credential_key() -> None:
    from zhiheng.bootstrap.settings import Settings

    key = Fernet.generate_key().decode()
    settings = Settings(
        database_url='postgresql+asyncpg://user:private-database-password@localhost/platform',
        redis_url='redis://localhost:6379/1',
        credential_key=key,
    )
    assert 'private-database-password' not in repr(settings)
    assert key not in repr(settings)


def test_app_has_a_health_route_without_initializing_database() -> None:
    from zhiheng.api.main import create_app
    from zhiheng.bootstrap.settings import Settings

    settings = Settings(
        database_url='postgresql+asyncpg://user:password@localhost/platform',
        redis_url='redis://localhost:6379/1',
        credential_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings)
    assert any(getattr(route, 'path', None) == '/health/live' for route in app.routes)
    assert not hasattr(app.state, 'container')
