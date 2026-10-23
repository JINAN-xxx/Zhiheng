import os
import subprocess
import sys
from pathlib import Path


def test_core_cli_lists_independent_process_commands() -> None:
    result = subprocess.run(
        [sys.executable, '-m', 'zhiheng', '--help'],
        env=os.environ
        | {
            'PYTHONPATH': str(Path(__file__).resolve().parents[3] / 'src'),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ['serve', 'worker', 'migrate', 'create-admin'])


def test_invalid_configuration_does_not_echo_secret_values() -> None:
    result = subprocess.run(
        [sys.executable, '-m', 'zhiheng', 'migrate'],
        env=os.environ
        | {
            'PYTHONPATH': str(Path(__file__).resolve().parents[3] / 'src'),
            'ZHIHENG_DATABASE_URL': 'private-configuration-input',
            'ZHIHENG_REDIS_URL': 'redis://localhost:6379',
            'ZHIHENG_CREDENTIAL_KEY': 'private-key-input',
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert 'private-configuration-input' not in result.stderr + result.stdout
    assert 'private-key-input' not in result.stderr + result.stdout
