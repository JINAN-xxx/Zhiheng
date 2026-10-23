"""Verify the new core and compatibility CLI are present in a built wheel."""

import argparse
import zipfile
from pathlib import Path

REQUIRED_FILES = frozenset({'zhiheng/cli/main.py', 'zhiheng/__init__.py', 'zhiheng/py.typed'})


def verify_core_wheel(directory: Path) -> Path:
    wheels = sorted(directory.glob('zhiheng_platform-*.whl'))
    if len(wheels) != 1:
        raise ValueError(f'Expected one platform wheel in {directory}, found {len(wheels)}')
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = REQUIRED_FILES - names
    if missing:
        raise ValueError(f'Core wheel is missing: {", ".join(sorted(missing))}')
    if any(
        name.startswith(('plugins/', 'config/', 'legacy/', 'cli/'))
        or any(part.startswith('.env') for part in Path(name).parts)
        for name in names
    ):
        raise ValueError('Core wheel includes legacy runtime or environment files')
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(f'Core wheel verified: {verify_core_wheel(args.directory)}')


if __name__ == '__main__':
    main()
