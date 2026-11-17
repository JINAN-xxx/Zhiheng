import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

from zhiheng.bootstrap.preflight import require_dependencies
from zhiheng.shared.errors import AppError


def test_dependency_preflight_rejects_missing_or_wrong_versions_without_importing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'requirements.in'
    path.write_text("example[extra]==2.0\nmissing>=1\nignored==1; python_version < '1.0'\n")

    def version(name: str) -> str:
        if name == 'missing':
            raise metadata.PackageNotFoundError(name)
        if name == 'ignored':
            raise AssertionError('inactive marker must not be loaded')
        return '1.0'

    monkeypatch.setattr(metadata, 'version', version)
    with pytest.raises(AppError) as error:
        require_dependencies(path)
    assert error.value.code == 'runtime_dependencies_mismatch'
    assert 'example' in error.value.message and 'missing' in error.value.message
    path.write_text('example[extra]>=1,<2\n')
    require_dependencies(path)


def test_cli_help_does_not_import_api_worker_or_supplier_sdks() -> None:
    code = """
import importlib.abc, runpy, sys
class BlockSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(('zhiheng.api', 'zhiheng.worker', 'openai', 'langchain', 'langgraph', 'pymilvus')):
            raise AssertionError('help imported ' + fullname)
sys.meta_path.insert(0, BlockSDK())
sys.argv = ['zhiheng', '--help']
runpy.run_module('zhiheng', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True,
        text=True,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[3] / 'src')},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'serve' in result.stdout and 'migrate' in result.stdout
