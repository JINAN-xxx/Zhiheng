"""Export the current platform source without Git history or private instance data."""

import argparse
import hashlib
import json
import re
import stat
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_ROOTS = frozenset(
    {
        '.git',
        '.worktrees',
        '.venv',
        'venv',
        'data',
        'logs',
        'vf_admin',
        '.runtime',
        'legacy',
        'backups',
        '_local_backups',
        '.test-artifacts',
        '.vscode',
        '.idea',
        '.codex',
        '.claude',
        '.cursor',
    }
)
EXCLUDED_PARTS = frozenset(
    {
        'node_modules',
        '__pycache__',
        'build',
        'dist',
        '.pytest_cache',
        '.ruff_cache',
        '.mypy_cache',
        '.vite',
        '.tox',
        '.nox',
        'test-results',
        'playwright-report',
    }
)
EXCLUDED_PREFIXES = ('docs/superpowers/', 'docs/testing/', 'frontend/design-previews/', 'deploy/core/.source/')
EXAMPLE_ENVIRONMENTS = frozenset({'.env.example', '.env.core.example'})
PRIVATE_SUFFIXES = frozenset({'.pem', '.key', '.p12', '.pfx', '.log', '.pid', '.db', '.sqlite', '.tar', '.gz', '.zip'})
PRIVATE_TEXT = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9]{30,}')


def excluded(name: str) -> bool:
    path = Path(name)
    return (
        not path.parts
        or path.parts[0] in EXCLUDED_ROOTS
        or any(part in EXCLUDED_PARTS or part.endswith('.egg-info') for part in path.parts)
        or name.startswith(EXCLUDED_PREFIXES)
        or name == '代码结构说明.md'
        or (path.name.startswith('.env') and path.name not in EXAMPLE_ENVIRONMENTS)
        or path.suffix.lower() in PRIVATE_SUFFIXES
    )


def source_files(root: Path) -> tuple[list[Path], int]:
    result = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        check=True,
        capture_output=True,
    )
    files, skipped = [], 0
    for name in sorted(set(result.stdout.decode().split(chr(0))) - {''}):
        path = root / name
        if excluded(name) or not path.exists():
            skipped += 1
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Source export refuses symlinks or paths outside the project: ' + name)
        if not path.is_file():
            raise ValueError('Source export requires regular files: ' + name)
        content = path.read_bytes()
        # Test fixtures may intentionally contain example credentials.
        if '/tests/' not in '/' + name and PRIVATE_TEXT.search(content.decode('utf-8', errors='ignore')):
            raise ValueError('Possible private credential; inspect this file before publishing: ' + name)
        files.append(path)
    return files, skipped


def export_source(root: Path, output: Path | None = None) -> dict[str, object]:
    root = root.resolve()
    files, skipped = source_files(root)
    if not files:
        raise ValueError('No source files found')
    manifest = {
        'format': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'git_history_included': False,
        'files': [
            {
                'path': path.relative_to(root).as_posix(),
                'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in files
        ],
    }
    summary: dict[str, object] = {
        'files': len(files),
        'source_bytes': sum(path.stat().st_size for path in files),
        'excluded_entries': skipped,
    }
    if output is not None:
        output = output.resolve()
        if output.is_relative_to(root):
            raise ValueError('Choose an output path outside the source project')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream, zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(root).as_posix())
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
            archive.writestr('SOURCE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_suffix(output.suffix + '.sha256').write_text(checksum + '  ' + output.name + '\n')
        summary.update({'output': str(output), 'sha256': checksum})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true', help='Check the current publication file selection')
    action.add_argument('--output', type=Path, help='Write a source ZIP outside the project')
    args = parser.parse_args()
    try:
        print(json.dumps(export_source(args.source_root, args.output), ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
