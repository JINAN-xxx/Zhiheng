import runpy
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

CHECKER_PATH = Path(__file__).resolve().parents[3] / 'tools/check_core_wheel.py'
REQUIRED_MEMBERS = (
    'zhiheng/cli/main.py',
    'zhiheng/__init__.py',
    'zhiheng/py.typed',
    *(
        f'zhiheng_platform-0.1.0.data/data/share/zhiheng/migrations/{name}'
        for name in ('env.py', 'versions/0001_core.py', 'versions/0002_developer.py')
    ),
)


@pytest.fixture
def verify_wheel() -> Callable[[Path], Path]:
    namespace = runpy.run_path(str(CHECKER_PATH))
    return cast('Callable[[Path], Path]', namespace['verify_core_wheel'])


def write_wheel(directory: Path, members: tuple[str, ...]) -> Path:
    path = directory / 'zhiheng_platform-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for member in members:
            archive.writestr(member, '')
    return path


def test_accepts_core_and_builtin_cli(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    wheel = write_wheel(tmp_path, REQUIRED_MEMBERS)

    assert verify_wheel(tmp_path) == wheel


@pytest.mark.parametrize(
    'forbidden',
    ['.env', 'zhiheng/.env.production', 'config/env.py', 'plugins/ai/hooks.py', 'legacy/backend/app.py', 'cli/main.py'],
)
def test_rejects_environment_or_legacy_files(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, forbidden: str
) -> None:
    write_wheel(tmp_path, (*REQUIRED_MEMBERS, forbidden))

    with pytest.raises(ValueError, match='legacy runtime or environment'):
        verify_wheel(tmp_path)


def test_rejects_a_wheel_missing_the_cli(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    write_wheel(tmp_path, ('zhiheng/__init__.py', 'zhiheng/py.typed'))

    with pytest.raises(ValueError, match=r'zhiheng/cli/main\.py'):
        verify_wheel(tmp_path)


def test_requires_both_database_migration_revisions(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    write_wheel(tmp_path, tuple(name for name in REQUIRED_MEMBERS if not name.endswith('0002_developer.py')))
    with pytest.raises(ValueError, match='migration'):
        verify_wheel(tmp_path)
