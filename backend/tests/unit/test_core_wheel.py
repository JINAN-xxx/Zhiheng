import runpy
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

CHECKER_PATH = Path(__file__).resolve().parents[3] / 'tools/check_core_wheel.py'
REQUIRED_MEMBERS = ('cli/main.py', 'zhiheng/__init__.py', 'zhiheng/py.typed')


@pytest.fixture
def verify_wheel() -> Callable[[Path], Path]:
    namespace = runpy.run_path(str(CHECKER_PATH))
    return cast('Callable[[Path], Path]', namespace['verify_core_wheel'])


def write_wheel(directory: Path, members: tuple[str, ...]) -> Path:
    path = directory / 'zhiheng_fastapi_backend_cli-1.10.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for member in members:
            archive.writestr(member, '')
    return path


def test_accepts_core_and_compatibility_cli(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    wheel = write_wheel(tmp_path, REQUIRED_MEMBERS)

    assert verify_wheel(tmp_path) == wheel


@pytest.mark.parametrize('forbidden', ['.env', 'zhiheng/.env.production', 'config/env.py', 'plugins/ai/hooks.py'])
def test_rejects_environment_or_legacy_files(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, forbidden: str
) -> None:
    write_wheel(tmp_path, (*REQUIRED_MEMBERS, forbidden))

    with pytest.raises(ValueError, match='legacy runtime or environment'):
        verify_wheel(tmp_path)


def test_rejects_a_wheel_missing_the_cli(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    write_wheel(tmp_path, ('zhiheng/__init__.py', 'zhiheng/py.typed'))

    with pytest.raises(ValueError, match=r'cli/main\.py'):
        verify_wheel(tmp_path)
