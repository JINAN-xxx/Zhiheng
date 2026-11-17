# ruff: noqa: PLC0415 -- Optional office libraries load only when the selected format needs them.
"""Trusted runtime helper. Only model subprocesses run inside strict isolated sessions.

This module is copied into the office runtime image. It deliberately imports no platform
configuration, database, model client or credentials.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import resource
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath

ROOT = Path('/workspace')
PRIVATE = Path('/run/zhiheng')
MAX_BYTES = 20 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 64
MAX_OUTPUT = 32768
MAX_ZIP_ENTRIES = 15000
MAX_PREVIEW_FILES = 5
MAX_PREVIEW_PAGES = 6
NONCE_LENGTH = 32
MAX_TIMEOUT = 120
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def path_parts(value: str, roots: tuple[str, ...]) -> tuple[str, ...]:
    parts = tuple(value.split('/'))
    if not parts or parts[0] not in roots or len(parts) == 1:
        raise ValueError('Invalid task file path')
    if any(part in {'', '.', '..'} for part in parts) or '\\' in value or '\x00' in value:
        raise ValueError('Invalid task file path')
    return parts


def open_parent(parts: tuple[str, ...], *, create: bool = False, original: bool = False) -> tuple[int, str]:
    descriptor = os.open(ROOT, DIR_FLAGS)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o755 if original else 0o700, dir_fd=descriptor)
                    os.chown(part, 0, 0, dir_fd=descriptor, follow_symlinks=False)
                except FileExistsError:
                    pass
            child = os.open(part, DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def read_checked(path: str) -> bytes:
    parts = path_parts(path, ('inputs', 'working', 'outputs'))
    parent, name = open_parent(parts)
    try:
        descriptor = os.open(name, READ_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError('File is not regular or exceeds 20 MiB')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                content = stream.read(MAX_BYTES + 1)
            after = os.fstat(descriptor)
            if len(content) != info.st_size or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError('File changed while being collected')
            return content
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def write_checked(path: str, content: bytes, *, original: bool = False) -> None:
    roots = ('inputs',) if original else ('working', 'outputs')
    parts = path_parts(path, roots)
    if len(content) > MAX_BYTES:
        raise ValueError('File exceeds 20 MiB')
    parent, name = open_parent(parts, create=True, original=original)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if original else os.O_TRUNC)
        try:
            descriptor = os.open(name, flags, 0o444 if original else 0o600, dir_fd=parent)
        except FileExistsError:
            if original and read_checked(path) == content:
                return
            raise ValueError('An immutable input already exists with different bytes') from None
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        os.chown(name, 0, 0, dir_fd=parent, follow_symlinks=False)
        os.chmod(name, 0o444 if original else 0o600, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def initialize() -> dict[str, object]:
    for name in ('inputs', 'working', 'outputs', '.environments', '.wheelhouse'):
        path = ROOT / name
        path.mkdir(exist_ok=True)
        if path.is_symlink():
            raise ValueError('Task directories must not be symbolic links')
        mutable = name in {'working', 'outputs', '.environments'}
        # userns mode maps the requested task UID to execd's outer UID; ownership
        # stays root in the outer container and appears as UID 10001 to the task.
        os.chown(path, 0, 0)
        os.chmod(path, 0o700 if mutable else 0o755)
    PRIVATE.mkdir(exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    return {'success': True}


def validate(content: bytes, path: str) -> dict[str, object]:
    suffix = PurePosixPath(path).suffix.lower()
    try:
        if not content:
            raise ValueError('Empty artifact')
        if suffix in {'.xlsx', '.docx'}:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ZIP_ENTRIES or sum(entry.file_size for entry in entries) > 150 * 1024 * 1024:
                    raise ValueError('Office archive is too large')
            if suffix == '.xlsx':
                from openpyxl import load_workbook

                workbook = load_workbook(io.BytesIO(content), read_only=True, keep_links=False)
                workbook.close()
            else:
                from docx import Document

                Document(io.BytesIO(content))
        elif suffix == '.pdf':
            from pypdf import PdfReader

            document = PdfReader(io.BytesIO(content))
            if document.is_encrypted or not document.pages:
                raise ValueError('Encrypted or empty PDF')
        elif suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        elif suffix in {'.csv', '.txt', '.md', '.json', '.py'}:
            content.decode('utf-8-sig')
        else:
            return {'valid': False, 'scope': '文件格式', 'message': '该格式暂未提供结构验证'}
    except Exception:
        return {'valid': False, 'scope': '文件结构', 'message': '文件无法按声明格式打开'}
    return {'valid': True, 'scope': '文件结构', 'message': '结构检查通过；业务数字与版式另行核对'}


def render(request: dict[str, object]) -> dict[str, object]:  # noqa: PLR0912, PLR0915 - bounded office format branches share the same immutable input
    """Run only in a fresh non-root isolated namespace, never on the backend host."""
    nonce = str(request['nonce'])
    if not re.fullmatch('[a-f0-9]{32}', nonce):
        raise ValueError('Invalid preview identity')
    folder = ROOT / 'working' / ('.previews-' + nonce)
    folder.mkdir(mode=0o700)
    prepared = []
    for number, path in enumerate((ROOT / 'outputs').rglob('*')):
        if number >= MAX_FILES:
            raise ValueError('Too many output files')
        if path.is_symlink():
            raise ValueError('Symbolic links cannot enter a task output')
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        content = read_checked(relative)
        validation = validate(content, relative)
        preview = []
        suffix = path.suffix.lower()
        if validation['valid']:
            try:
                target = folder / (str(number) + '.pdf')
                source_pdf = None
                if suffix in {'.docx', '.xlsx'} and number < MAX_PREVIEW_FILES:
                    with tempfile.TemporaryDirectory(prefix='office-preview-', dir=folder) as temporary:
                        temporary_path = Path(temporary)
                        source = temporary_path / ('source' + suffix)
                        source.write_bytes(content)
                        subprocess.run(
                            [
                                'libreoffice',
                                '-env:UserInstallation=' + (temporary_path / 'profile').as_uri(),
                                '--headless',
                                '--nologo',
                                '--nodefault',
                                '--norestore',
                                '--convert-to',
                                'pdf',
                                '--outdir',
                                str(temporary_path),
                                str(source),
                            ],
                            capture_output=True,
                            check=True,
                            timeout=35,
                        )
                        produced = temporary_path / 'source.pdf'
                        if produced.is_file():
                            source_pdf = produced.read_bytes()
                elif suffix == '.pdf':
                    source_pdf = content
                if source_pdf is not None:
                    from pypdf import PdfReader, PdfWriter

                    reader = PdfReader(io.BytesIO(source_pdf))
                    writer = PdfWriter()
                    for page in reader.pages[:MAX_PREVIEW_PAGES]:
                        writer.add_page(page)
                    with target.open('wb') as stream:
                        writer.write(stream)
                    if target.stat().st_size <= MAX_BYTES:
                        preview.append(target.relative_to(ROOT).as_posix())
                        validation.update(
                            preview_pages=min(MAX_PREVIEW_PAGES, len(reader.pages)),
                            preview_truncated=len(reader.pages) > MAX_PREVIEW_PAGES,
                            preview='PDF 预览；未作自动视觉判读',
                        )
                elif suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
                    from PIL import Image

                    image_target = folder / (str(number) + '.png')
                    with Image.open(io.BytesIO(content)) as image:
                        image.thumbnail((1600, 1600))
                        image.convert('RGB').save(image_target)
                    if image_target.stat().st_size <= MAX_BYTES:
                        preview.append(image_target.relative_to(ROOT).as_posix())
            except Exception:
                validation['preview'] = '预览暂不可用，原始成果可下载核对'
        prepared.append(
            {
                'path': relative,
                'sha256': hashlib.sha256(content).hexdigest(),
                'validation': validation,
                'previews': preview,
            }
        )
    return {'success': True, 'prepared': prepared}


def collect(request: dict[str, object]) -> dict[str, object]:  # noqa: PLR0912 - preserve every no-follow/hash/size guard in the publication boundary
    roots = ('working', 'outputs') if request.get('working') else ('outputs',)
    nonce = str(request['nonce'])
    if len(nonce) != NONCE_LENGTH or any(character not in '0123456789abcdef' for character in nonce):
        raise ValueError('Invalid snapshot identity')
    destination = PRIVATE / nonce
    destination.mkdir(mode=0o700)
    entries = []
    prepared = {item['path']: item for item in request.get('prepared', [])}
    total = 0
    for root in roots:
        folder = ROOT / root
        if folder.is_symlink():
            raise ValueError('Task directory was replaced with a symbolic link')
        for current, directories, files in os.walk(folder, followlinks=False):
            if root == 'working':
                directories[:] = [name for name in directories if not re.fullmatch(r'\.previews-[a-f0-9]{32}', name)]
            for name in directories:
                if (Path(current) / name).is_symlink():
                    raise ValueError('Symbolic links cannot enter a task snapshot')
            for name in sorted(files):
                path = (Path(current) / name).relative_to(ROOT).as_posix()
                content = read_checked(path)
                total += len(content)
                if total > MAX_TOTAL or len(entries) >= MAX_FILES:
                    raise ValueError('Task snapshot exceeds its file or byte limit')
                target = destination / str(len(entries))
                target.write_bytes(content)
                target.chmod(0o600)
                digest = hashlib.sha256(content).hexdigest()
                facts = prepared.get(path)
                if not request.get('working') and (facts is None or facts['sha256'] != digest):
                    raise ValueError('Output changed after isolated validation')
                previews = []
                for index, preview_path in enumerate(facts.get('previews', []) if facts else []):
                    if not re.fullmatch('working/\\.previews-[a-f0-9]{32}/[0-9]+\\.(pdf|png)', preview_path):
                        raise ValueError('Invalid generated preview path')
                    preview_bytes = read_checked(preview_path)
                    total += len(preview_bytes)
                    if total > MAX_TOTAL:
                        raise ValueError('Preview exceeds task snapshot size')
                    preview_target = destination / (str(len(entries)) + '-preview-' + str(index))
                    preview_target.write_bytes(preview_bytes)
                    preview_target.chmod(0o600)
                    previews.append(
                        {
                            'path': preview_path,
                            'snapshot_path': str(preview_target),
                            'size': len(preview_bytes),
                            'sha256': hashlib.sha256(preview_bytes).hexdigest(),
                            'mime': mimetypes.guess_type(preview_path)[0] or 'application/octet-stream',
                        }
                    )
                entries.append(
                    {
                        'path': path,
                        'snapshot_path': str(target),
                        'size': len(content),
                        'sha256': digest,
                        'mime': mimetypes.guess_type(path)[0] or 'application/octet-stream',
                        'validation': facts.get('validation', {}) if facts else {},
                        'previews': previews,
                    }
                )
    return {'success': True, 'files': entries}


def run(request: dict[str, object]) -> dict[str, object]:
    timeout = int(request['timeout'])
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError('Invalid command timeout')
    python = str(request.get('python') or sys.executable)
    argv = (
        [python, '-c', str(request['code'])] if request['kind'] == 'python' else ['/bin/sh', '-c', str(request['code'])]
    )
    environment = {
        'PATH': os.path.dirname(python) + ':/usr/local/bin:/usr/bin:/bin',
        'LANG': 'C.UTF-8',
        'HOME': '/workspace/working',
        'PYTHONUTF8': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
    process = subprocess.Popen(
        argv,
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
    selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + timeout
    timed_out = truncated = False
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            for event, _ in selector.select(timeout=min(0.1, max(0, deadline - time.monotonic()))):
                block = os.read(event.fileobj.fileno(), 4096)
                if not block:
                    selector.unregister(event.fileobj)
                    continue
                remaining = MAX_OUTPUT - sum(len(value) for value in output.values())
                output[event.data].extend(block[:remaining])
                if len(block) > remaining:
                    truncated = True
                    break
            if truncated:
                break
        if not timed_out and not truncated:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return {
        'success': True,
        'exit_code': 124 if timed_out else 125 if truncated else process.returncode,
        'stdout': output['stdout'].decode(errors='replace'),
        'stderr': output['stderr'].decode(errors='replace'),
        'timed_out': timed_out,
        'truncated': truncated,
    }


def main() -> None:  # noqa: PLR0912, PLR0915 - the fixed trusted protocol does not dispatch arbitrary imports or executable names
    request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
    operation = request['operation']
    if operation == 'initialize':
        result = initialize()
    elif operation == 'write':
        stage = str(request['stage'])
        if not re.fullmatch('/run/zhiheng/upload-[a-f0-9]{32}', stage):
            raise ValueError('Invalid upload staging path')
        source = Path(stage)
        try:
            if source.is_symlink() or source.stat().st_size > MAX_BYTES:
                raise ValueError('Invalid staged input')
            write_checked(request['path'], source.read_bytes(), original=request.get('original', False))
        finally:
            source.unlink(missing_ok=True)
        result = {'success': True}
    elif operation == 'collect':
        result = collect(request)
    elif operation == 'render':
        result = render(request)
    elif operation == 'read':
        content = read_checked(request['path'])
        limit = int(request.get('limit', MAX_BYTES))
        if len(content) > limit:
            raise ValueError('File exceeds the requested read limit')
        nonce = str(request['nonce'])
        if not re.fullmatch('[a-f0-9]{32}', nonce):
            raise ValueError('Invalid snapshot identity')
        folder = PRIVATE / nonce
        folder.mkdir(mode=0o700)
        (folder / '0').write_bytes(content)
        result = {
            'success': True,
            'files': [
                {
                    'path': request['path'],
                    'snapshot_path': str(folder / '0'),
                    'size': len(content),
                    'sha256': hashlib.sha256(content).hexdigest(),
                    'mime': mimetypes.guess_type(request['path'])[0] or 'application/octet-stream',
                    'validation': {},
                }
            ],
        }
    elif operation == 'cleanup_snapshot':
        nonce = str(request['nonce'])
        if not re.fullmatch('[a-f0-9]{32}', nonce):
            raise ValueError('Invalid snapshot identity')
        shutil.rmtree(PRIVATE / nonce, ignore_errors=True)
        shutil.rmtree(ROOT / 'working' / ('.previews-' + nonce), ignore_errors=True)
        result = {'success': True}
    elif operation == 'list':
        path = str(request.get('path', '.'))
        if path != '.':
            parts = path_parts(path + '/placeholder', ('inputs', 'working', 'outputs'))
            descriptor, _ = open_parent(parts)
        else:
            descriptor = os.open(ROOT, DIR_FLAGS)
        try:
            result = {
                'success': True,
                'entries': [
                    {
                        'name': entry.name,
                        'directory': entry.is_dir(follow_symlinks=False),
                        'bytes': entry.stat(follow_symlinks=False).st_size,
                    }
                    for entry in list(os.scandir(descriptor))[:100]
                    if not entry.name.startswith('.')
                ],
            }
        finally:
            os.close(descriptor)
    elif operation == 'run':
        result = run(request)
    else:
        raise ValueError('Unsupported trusted helper operation')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'success': False, 'error': type(error).__name__, 'message': str(error)[:1000]}))
