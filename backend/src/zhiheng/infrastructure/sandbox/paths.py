"""Opaque task-local paths accepted by sandbox tools and checkpoint transport."""

from pathlib import PurePosixPath
from unicodedata import category

from zhiheng.shared.errors import AppError

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024
MAX_SNAPSHOT_FILES = 64
MAX_COMMAND_SECONDS = 120
MAX_COMMAND_OUTPUT = 32768
MAX_PATH_BYTES = 1024


def sandbox_path(
    value: str, *, roots: tuple[str, ...] = ('inputs', 'working', 'outputs'), directory: bool = False
) -> str:
    if any(category(character) in {'Cc', 'Cs'} for character in value):
        raise AppError('sandbox_invalid_path', '工作文件路径不能包含控制字符', 422)
    raw = value.removeprefix('/workspace/').strip()
    if directory and raw in {'', '.', '/workspace'}:
        return '.'
    parts = raw.split('/')
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {'', '.', '..'} for part in parts)
        or any(character in raw for character in ('\\', '\x00'))
        or path.parts[0] not in roots
        or len(raw.encode()) > MAX_PATH_BYTES
    ):
        raise AppError('sandbox_invalid_path', '只能访问当前任务的资料、工作文件和成果', 422)
    if not directory and len(parts) == 1:
        raise AppError('sandbox_invalid_path', '请指定具体文件', 422)
    return path.as_posix()
