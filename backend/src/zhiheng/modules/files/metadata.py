"""Permanent file metadata and canonical upload semantics."""

import hashlib

from zhiheng.modules.files.service import sniff_mime


def file_semantics(filename: str, content: bytes, purpose: str) -> dict[str, object]:
    return {
        'filename': filename,
        'purpose': purpose,
        'mime': sniff_mime(content, filename),
        'sha256': hashlib.sha256(content).hexdigest(),
    }
