"""Generate independent MinIO administrator/application pairs without rotating existing secrets."""

import os
import secrets
import sys
import tempfile
from pathlib import Path

PAIRS = (
    ('CORE_FILES_ROOT_USER', 'CORE_FILES_ROOT_PASSWORD'),
    ('CORE_FILES_ACCESS_KEY', 'CORE_FILES_SECRET_KEY'),
)
DEFAULTS = {
    'CORE_FILES_S3_ENDPOINT': 'http://file-objects:9000',
    'CORE_FILES_BUCKET': 'zhiheng-files',
    'CORE_FILES_S3_REGION': 'us-east-1',
    'CORE_FILES_S3_TIMEOUT_SECONDS': '30',
    'CORE_FILES_CONSOLE_BIND': '127.0.0.1',
    'CORE_FILES_CONSOLE_PORT': '19001',
}


def _parse(content: str) -> dict[str, str]:
    values = {}
    fields = {*DEFAULTS, *(key for pair in PAIRS for key in pair)}
    for line in content.splitlines():
        key, separator, value = line.partition('=')
        key = key.strip().removeprefix('export ').strip()
        if separator and key in fields:
            if key in values:
                raise ValueError(f'Duplicate {key}; resolve the configuration before retrying.')
            values[key] = value.split(' #', 1)[0].strip().strip('"\'')
    return values


def initialize(target: Path) -> bool:
    if not target.is_file():
        raise ValueError('Core configuration is missing; run ./deploy.sh init first.')
    content = target.read_text()
    values = _parse(content)
    additions = {}
    for pair in PAIRS:
        if all(values.get(key) for key in pair):
            continue
        if any(values.get(key) for key in pair):
            raise ValueError(
                'Incomplete MinIO credential pair; restore the missing value without rotating credentials.'
            )
        additions.update(zip(pair, (secrets.token_urlsafe(24), secrets.token_urlsafe(36)), strict=False))
    combined = values | additions
    if combined['CORE_FILES_ROOT_USER'] == combined['CORE_FILES_ACCESS_KEY']:
        raise ValueError('Business MinIO must use a dedicated application account, separate from root.')
    additions.update({key: value for key, value in DEFAULTS.items() if not values.get(key)})
    if not additions:
        return False
    lines = []
    for line in content.splitlines():
        key, separator, _ = line.partition('=')
        key = key.strip().removeprefix('export ').strip()
        lines.append(f'{key}={additions[key]}' if separator and key in additions else line)
    lines.extend(f'{key}={value}' for key, value in additions.items() if key not in values)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, prefix='.minio-env-', delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main() -> int:
    try:
        changed = initialize(Path(sys.argv[1]).expanduser().resolve())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        'Business MinIO configuration initialized; existing credentials preserved.'
        if changed
        else 'Business MinIO configuration already complete; preserved unchanged.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
