import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from zhiheng.bootstrap.file_storage import create_file_storage
from zhiheng.bootstrap.settings import Settings
from zhiheng.shared.errors import AppError

ROOT = Path(__file__).resolve().parents[3]


def initialize(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / 'tools/init_minio.py'), str(path)], capture_output=True, text=True, check=False
    )


def test_upgrade_generates_bucket_account_without_rotating_admin_or_platform_secrets(tmp_path: Path) -> None:
    config = tmp_path / 'core.env'
    before = 'CORE_DB_PASSWORD=keep-db\nCORE_CREDENTIAL_KEY=keep-key\nCORE_FILES_ROOT_USER=keep-root\nCORE_FILES_ROOT_PASSWORD=keep-root-secret\n'
    config.write_text(before)
    assert initialize(config).returncode == 0
    content = config.read_text()
    assert content.startswith(before)
    values = dict(line.split('=', 1) for line in content.splitlines())
    assert values['CORE_FILES_ACCESS_KEY'] != values['CORE_FILES_ROOT_USER']
    assert values['CORE_FILES_SECRET_KEY'] != values['CORE_FILES_ROOT_PASSWORD']
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert initialize(config).returncode == 0 and config.read_text() == content


@pytest.mark.parametrize(
    'content',
    [
        'CORE_FILES_ROOT_USER=existing\n',
        'CORE_FILES_ROOT_USER=a\nCORE_FILES_ROOT_PASSWORD=b\nCORE_FILES_ACCESS_KEY=partial\n',
        'CORE_FILES_ROOT_USER=a\nCORE_FILES_ROOT_PASSWORD=b\nCORE_FILES_ACCESS_KEY=a\nCORE_FILES_SECRET_KEY=c\n',
    ],
)
def test_incomplete_or_reused_root_credentials_are_never_silently_replaced(tmp_path: Path, content: str) -> None:
    config = tmp_path / 'core.env'
    config.write_text(content)
    result = initialize(config)
    assert result.returncode != 0 and config.read_text() == content
    assert 'existing' not in result.stdout + result.stderr


def configuration(**overrides: str) -> Settings:
    return Settings(
        **{
            'database_url': 'postgresql+asyncpg://u:p@localhost/test',
            'redis_url': 'redis://localhost/0',
            'credential_key': Fernet.generate_key().decode(),
            **overrides,
        }
    )


def test_application_startup_requires_dedicated_object_storage_credentials() -> None:
    with pytest.raises(AppError) as error:
        create_file_storage(configuration())
    assert error.value.code == 'file_storage_unconfigured'


@pytest.mark.parametrize(
    'endpoint',
    ['file:///tmp/files', 'http://user:secret@minio:9000', 'http://minio:9000/bucket', 'http://minio:9000?key=secret'],
)
def test_storage_endpoint_is_an_http_origin_without_embedded_credentials(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        configuration(files_s3_endpoint=endpoint)
