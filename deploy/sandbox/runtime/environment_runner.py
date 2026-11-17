"""Download binary wheels as trusted preparation; install them only inside the isolated task."""

import base64
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import zlib
from pathlib import Path

from packaging.requirements import Requirement

WHEELS = Path('/workspace/.wheelhouse')
ENVIRONMENTS = Path('/workspace/.environments')
MAX_WHEELS_BYTES = 150 * 1024 * 1024


def main() -> None:  # noqa: PLR0912, PLR0915 - keep the trusted download/install protocol in one auditable dispatcher
    request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
    requirements = [Requirement(item) for item in request['packages']]
    if any(item.url for item in requirements):
        raise ValueError('Only indexed binary-wheel dependencies are supported')
    packages = sorted(str(item) for item in requirements)
    key = hashlib.sha256(json.dumps({'python': sys.version, 'packages': packages}).encode()).hexdigest()[:24]
    target = ENVIRONMENTS / key
    manifest = target / 'environment.json'
    if request['operation'] == 'download':
        missing = []
        resolved = []
        for item in requirements:
            if item.marker is not None and not item.marker.evaluate():
                continue
            if item.extras:
                missing.append(str(item))
                continue
            try:
                version = importlib.metadata.version(item.name)
            except importlib.metadata.PackageNotFoundError:
                missing.append(str(item))
                continue
            if item.specifier and version not in item.specifier:
                missing.append(str(item))
            else:
                resolved.append(item.name + '==' + version)
        if not missing:
            print(json.dumps({'success': True, 'python': sys.executable, 'resolved': resolved}))
            return
        wheelhouse = WHEELS / key
        wheelhouse.mkdir(parents=True, exist_ok=True, mode=0o755)
        result = subprocess.run(
            [
                sys.executable,
                '-I',
                '-m',
                'pip',
                'download',
                '--no-input',
                '--no-cache-dir',
                '--disable-pip-version-check',
                '--only-binary=:all:',
                '--index-url',
                request['index'],
                '--dest',
                str(wheelhouse),
                *missing,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=150,
        )
        if result.returncode:
            raise ValueError('无法准备二进制依赖：' + result.stderr[-3000:])
        files = list(wheelhouse.iterdir())
        if sum(path.stat().st_size for path in files) > MAX_WHEELS_BYTES:
            raise ValueError('Dependency wheels exceed the preparation limit')
        for path in files:
            if path.is_symlink() or path.suffix != '.whl':
                raise ValueError('Dependency preparation returned a non-wheel file')
            path.chmod(0o444)
        print(json.dumps({'success': True, 'missing': missing}))
        return
    if request['operation'] != 'install':
        raise ValueError('Unsupported dependency operation')
    if manifest.is_file() and (target / 'bin/python').is_file():
        data = json.loads(manifest.read_text())
        if data.get('packages') == packages and data.get('python') == str(target / 'bin/python'):
            print(json.dumps({'success': True, **data}))
            return
    subprocess.run(
        [sys.executable, '-I', '-m', 'venv', '--system-site-packages', str(target)],
        check=True,
        capture_output=True,
        timeout=25,
    )
    python = str(target / 'bin/python')
    result = subprocess.run(
        [
            python,
            '-I',
            '-m',
            'pip',
            'install',
            '--no-input',
            '--no-cache-dir',
            '--disable-pip-version-check',
            '--no-index',
            '--only-binary=:all:',
            '--find-links',
            str(WHEELS / key),
            *packages,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise ValueError('无法安装任务依赖：' + result.stderr[-3000:])
    frozen = subprocess.run(
        [python, '-I', '-m', 'pip', 'freeze'], check=True, capture_output=True, text=True, timeout=15
    ).stdout.splitlines()
    data = {'python': python, 'packages': packages, 'resolved': frozen}
    manifest.write_text(json.dumps(data))
    print(json.dumps({'success': True, **data}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'success': False, 'message': str(error)[:3000]}))
