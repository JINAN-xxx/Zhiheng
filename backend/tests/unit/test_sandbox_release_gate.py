"""Exercise release exit status without a running sandbox or database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ('body', 'pytest_args', 'status'),
    [
        ('def test_ok():\n    assert True\n', [], pytest.ExitCode.OK),
        ('def test_ok():\n    assert False\n', [], pytest.ExitCode.TESTS_FAILED),
        ('def test_ok():\n    pytest.skip("fixture missing")\n', [], pytest.ExitCode.TESTS_FAILED),
        ('@pytest.mark.xfail\ndef test_ok():\n    assert False\n', [], pytest.ExitCode.TESTS_FAILED),
        (
            'def test_ok():\n    assert True\ndef test_unselected():\n    assert True\n',
            ['-k', 'test_ok'],
            pytest.ExitCode.TESTS_FAILED,
        ),
        ('', [], pytest.ExitCode.NO_TESTS_COLLECTED),
    ],
)
def test_release_gate_exit_status(tmp_path: Path, body: str, pytest_args: list[str], status: pytest.ExitCode) -> None:
    tests = tmp_path / 'test_subject.py'
    tests.write_text('import pytest\n\n' + body, encoding='utf-8')
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'pytest',
            '-q',
            '-p',
            'tests.core.sandbox_release_gate',
            str(tests),
            *pytest_args,
        ],
        cwd=BACKEND,
        env={**os.environ, 'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == status, result.stdout + result.stderr
    if 'skip' in body or 'xfail' in body or pytest_args:
        assert 'requires every selected case to pass' in result.stdout
