"""Publication snapshots preserve current source and exclude private/history files."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

EXPORTER = Path(__file__).resolve().parents[3] / 'tools' / 'export_source.py'


def command(*args: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=root, capture_output=True, text=True, check=True)


def repository(root: Path) -> None:
    root.mkdir()
    command('git', 'init', '-q', root=root)
    for name in ('README.md', 'old.py', '.env', 'data/private.txt', 'docs/superpowers/plan.md'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('local test fixture')
    command('git', 'add', '-A', root=root)
    (root / 'old.py').unlink()
    (root / 'current.py').write_text('CURRENT = True\n')


def test_export_uses_current_files_and_omits_tracked_private_history(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    repository(root)
    output = tmp_path / 'release.zip'
    result = command(sys.executable, str(EXPORTER), '--source-root', str(root), '--output', str(output), root=tmp_path)
    assert json.loads(result.stdout)['files'] == 2
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {'README.md', 'current.py', 'SOURCE_MANIFEST.json'}
        manifest = json.loads(archive.read('SOURCE_MANIFEST.json'))
        assert manifest['git_history_included'] is False
        assert archive.read('current.py') == b'CURRENT = True\n'
    assert output.with_suffix('.zip.sha256').is_file()


def test_export_check_creates_no_files_and_output_must_be_outside_project(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    repository(root)
    result = command(sys.executable, str(EXPORTER), '--source-root', str(root), '--check', root=tmp_path)
    assert json.loads(result.stdout)['files'] == 2
    assert not (root / 'SOURCE_MANIFEST.json').exists()
    rejected = subprocess.run(
        [sys.executable, str(EXPORTER), '--source-root', str(root), '--output', str(root / 'bad.zip')],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0 and not (root / 'bad.zip').exists()


def test_export_refuses_private_key_content_outside_test_fixtures(tmp_path: Path) -> None:
    root = tmp_path / 'source'
    repository(root)
    (root / 'connection.py').write_text('-----BEGIN ' + 'PRIVATE KEY-----')
    result = subprocess.run(
        [sys.executable, str(EXPORTER), '--source-root', str(root), '--check'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert 'connection.py' in result.stderr and 'Possible private credential' in result.stderr
