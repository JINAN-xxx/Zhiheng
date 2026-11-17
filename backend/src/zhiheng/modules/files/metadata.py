"""Permanent file metadata and canonical upload semantics."""

import hashlib
import re

from zhiheng.shared.csv_text import decode_csv_text
from zhiheng.shared.errors import AppError

MAX_FILENAME_LENGTH = 255
FIRST_PRINTABLE = 32
DELETE_CONTROL = 127
IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


def validate_filename(filename: str) -> str:
    if (
        not filename.strip()
        or len(filename) > MAX_FILENAME_LENGTH
        or filename in {'.', '..'}
        or any(ord(char) < FIRST_PRINTABLE or ord(char) == DELETE_CONTROL for char in filename)
        or any(char in filename for char in ('/', '\\', '\x00'))
        or re.search(r'%[0-9a-fA-F]{2}', filename)
    ):
        raise AppError('invalid_file_name', '文件名无效', 422)
    return filename


def sniff_mime(content: bytes, filename: str) -> str:
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp'
    if content.startswith(b'%PDF-'):
        return 'application/pdf'
    extension = filename.rpartition('.')[2].lower()
    if content.startswith(b'PK\x03\x04'):
        return {
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }.get(extension, 'application/zip')
    if extension == 'csv':
        try:
            decode_csv_text(content)
            return 'text/csv'
        except (UnicodeError, ValueError):
            return 'application/octet-stream'
    if b'\x00' not in content[:8192]:
        try:
            content.decode('utf-8-sig')
            return {
                'json': 'application/json',
                'html': 'text/html',
                'htm': 'text/html',
                'md': 'text/markdown',
                'svg': 'image/svg+xml',
            }.get(extension, 'text/plain')
        except UnicodeError:
            pass
    return 'application/octet-stream'


def file_semantics(filename: str, content: bytes, purpose: str) -> dict[str, object]:
    return {
        'filename': filename,
        'purpose': purpose,
        'mime': sniff_mime(content, filename),
        'sha256': hashlib.sha256(content).hexdigest(),
    }
