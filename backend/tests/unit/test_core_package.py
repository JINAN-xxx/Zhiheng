import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_core_import_has_no_legacy_or_framework_startup(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(BACKEND_ROOT / 'src')
    result = subprocess.run(
        [
            sys.executable,
            '-B',
            '-c',
            (
                'import sys; import zhiheng; '
                "assert 'config.env' not in sys.modules; "
                "assert 'plugins' not in sys.modules; "
                "assert 'sqlalchemy' not in sys.modules; "
                "assert 'fastapi' not in sys.modules"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_core_package_ships_type_information() -> None:
    assert (BACKEND_ROOT / 'src/zhiheng/py.typed').is_file()
