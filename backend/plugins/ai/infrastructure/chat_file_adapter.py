# ruff: noqa: PLC0415
"""Read owned managed uploads without exposing filesystem paths to chat clients."""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import os
import stat
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

from sqlalchemy import or_, select

from config.env import UploadConfig
from module_admin.entity.do.file_do import SysFileInfo
from plugins.ai.domain.chat_files import (
    CHAT_ATTACHMENT_EXTENSIONS,
    MAX_CHAT_ATTACHMENT_BYTES,
    MAX_CHAT_ATTACHMENT_TEXT_CHARS,
    ResolvedPlatformAttachment,
    ResolvedPlatformImage,
)
from plugins.ai.service.platform_chat_service import PlatformChatError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_MAX_IMAGE_PIXELS = 25_000_000
_MAX_IMAGE_FRAMES = 100
_MAX_ZIP_ENTRIES = 2_000
_MAX_EXPANDED_MEMBER_BYTES = 8 * 1024 * 1024
_MAX_EXPANDED_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200
_MAX_PDF_PAGES = 100
_MAX_PDF_RESOURCE_DEPTH = 16
_MAX_PDF_RESOURCE_OBJECTS = 1_000
_MAX_SHEET_ROWS = 10_000
_MAX_SHEET_COLUMNS = 200
_MAX_SHEET_CELLS = 50_000
_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.csv': 'text/csv',
}
_IMAGE_MIME_TYPES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}


class _FileTooLarge(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _ManagedFile:
    root: str
    storage_key: str
    file_name: str
    extension: str
    declared_size: int


class ChatFileAdapter:
    """Resolve user-owned active uploads through an independent read session."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def resolve(self, user_id: int, file_id: UUID, *, max_size_bytes: int) -> ResolvedPlatformImage:
        try:
            managed = await self._lookup(user_id, file_id)
            size_limit = _size_limit(max_size_bytes, _MAX_IMAGE_BYTES)
            return await asyncio.to_thread(_resolve_image, managed, file_id, size_limit)
        except Exception:
            raise PlatformChatError.image_invalid() from None

    async def resolve_attachment(
        self, user_id: int, file_id: UUID, *, max_size_bytes: int
    ) -> ResolvedPlatformAttachment:
        try:
            managed = await self._lookup(user_id, file_id)
            size_limit = _size_limit(max_size_bytes, MAX_CHAT_ATTACHMENT_BYTES)
            return await asyncio.to_thread(_resolve_attachment, managed, file_id, size_limit)
        except _FileTooLarge:
            raise PlatformChatError.attachment_too_large() from None
        except Exception:
            raise PlatformChatError.attachment_invalid() from None

    async def _lookup(self, user_id: int, file_id: UUID) -> _ManagedFile:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError('Invalid owner')
        async with self._session_factory() as session:
            item = await session.scalar(
                select(SysFileInfo).where(
                    SysFileInfo.file_id == str(file_id),
                    SysFileInfo.owner_user_id == user_id,
                    SysFileInfo.status == 'active',
                    SysFileInfo.del_flag == '0',
                    SysFileInfo.storage_type == 'local',
                    SysFileInfo.access_type.in_(('public', 'private')),
                    or_(SysFileInfo.expire_time.is_(None), SysFileInfo.expire_time > datetime.now()),
                )
            )
            if item is None:
                raise ValueError('File unavailable')
            root = UploadConfig.UPLOAD_PATH if item.access_type == 'public' else UploadConfig.PRIVATE_UPLOAD_PATH
            return _ManagedFile(
                root=str(root),
                storage_key=item.storage_key,
                file_name=_display_name(item.original_name),
                extension='.' + item.extension.lower().lstrip('.'),
                declared_size=item.file_size,
            )


def _size_limit(requested: int, maximum: int) -> int:
    if type(requested) is not int or requested <= 0:
        raise ValueError('Invalid size limit')
    return min(requested, maximum)


def _display_name(name: str) -> str:
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(character for character in name if not unicodedata.category(character).startswith('C'))
    return name.strip()[:255] or 'attachment'


def _read_owned_file(managed: _ManagedFile, max_size_bytes: int) -> bytes:
    """Walk from a trusted root using dirfds; never follow a replaced symlink."""
    key = managed.storage_key
    parts = key.split('/')
    if not key or '\\' in key or ':' in key or any(part in {'', '.', '..'} for part in parts):
        raise ValueError('Invalid storage key')
    if managed.declared_size < 0:
        raise ValueError('Invalid file size')
    if managed.declared_size > max_size_bytes:
        raise _FileTooLarge
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(managed.root, directory_flags)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(file_fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('File unavailable')
            if metadata.st_size > max_size_bytes:
                raise _FileTooLarge
            content = stream.read(max_size_bytes + 1)
    finally:
        os.close(directory_fd)
    if len(content) > max_size_bytes:
        raise _FileTooLarge
    if not content:
        raise ValueError('Empty file')
    return content


def _resolve_image(managed: _ManagedFile, file_id: UUID, max_size_bytes: int) -> ResolvedPlatformImage:
    from PIL import Image

    content = _read_owned_file(managed, max_size_bytes)
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as image:
            mime_type = _IMAGE_MIME_TYPES.get(image.format)
            if mime_type is None or image.width * image.height > _MAX_IMAGE_PIXELS:
                raise ValueError('Invalid image')
            image.verify()
        # verify() checks the container; decoding also rejects truncated JPEG data.
        with Image.open(io.BytesIO(content)) as image:
            frame_count = getattr(image, 'n_frames', 1)
            if frame_count > _MAX_IMAGE_FRAMES or image.width * image.height * frame_count > _MAX_IMAGE_PIXELS:
                raise ValueError('Invalid image dimensions')
            for frame in range(frame_count):
                image.seek(frame)
                image.load()
    return ResolvedPlatformImage(
        file_id=file_id,
        mime_type=mime_type,
        size_bytes=len(content),
        data_url=f'data:{mime_type};base64,{base64.b64encode(content).decode("ascii")}',
        file_name=managed.file_name,
    )


class _BoundedText:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0

    def append(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.size += len(text) + bool(self.parts)
        if self.size > MAX_CHAT_ATTACHMENT_TEXT_CHARS:
            raise _FileTooLarge
        self.parts.append(text)

    def finish(self) -> str:
        text = '\n'.join(self.parts)
        if not text or any(unicodedata.category(char) == 'Cc' and char not in '\t\r\n' for char in text):
            raise ValueError('No readable text')
        return text

    def append_row(self, values: Iterable[object]) -> None:
        cells = ['' if value is None else str(value) for value in values]
        if any(cell.strip() for cell in cells):
            self.append(' | '.join(cells))


def _decode_text(content: bytes) -> str:
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('gb18030')
    if any(unicodedata.category(char) == 'Cc' and char not in '\t\r\n' for char in text):
        raise ValueError('Binary content')
    return text


class _NoDoctypeBuilder(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError('DTD is not supported')


def _check_office_archive(content: bytes, required_member: str) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > _MAX_ZIP_ENTRIES:
            raise _FileTooLarge
        total = 0
        seen: set[str] = set()
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (
                name in seen
                or path.is_absolute()
                or '..' in path.parts
                or '\\' in name
                or member.flag_bits & 1
                or stat.S_ISLNK(member.external_attr >> 16)
                or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise ValueError('Invalid archive entry')
            seen.add(name)
            total += member.file_size
            if (
                member.file_size > _MAX_EXPANDED_MEMBER_BYTES
                or total > _MAX_EXPANDED_TOTAL_BYTES
                or member.file_size > max(member.compress_size, 1) * _MAX_COMPRESSION_RATIO
            ):
                raise _FileTooLarge
            if name.endswith(('.xml', '.rels')):
                with archive.open(member) as stream:
                    xml = stream.read(_MAX_EXPANDED_MEMBER_BYTES + 1)
                if len(xml) > _MAX_EXPANDED_MEMBER_BYTES:
                    raise _FileTooLarge
                ET.fromstring(xml, parser=ET.XMLParser(target=_NoDoctypeBuilder()))
        if required_member not in seen or '[Content_Types].xml' not in seen:
            raise ValueError('Invalid document format')


class _PdfContentBudget:
    """Bound only streams that pypdf text extraction can decode, per document."""

    def __init__(self, reader: Any) -> None:
        self.reader = reader
        self.decoded_bytes = 0
        self.stream_sizes: dict[int, int] = {}
        self.visited_resources: set[tuple[str, int]] = set()
        self.active_forms: set[int] = set()

    def _stream_size(self, stream: Any) -> int:
        from pypdf.generic import StreamObject

        stream = stream.get_object()
        if not isinstance(stream, StreamObject):
            raise ValueError('Invalid PDF stream')
        identity = id(stream)
        if identity not in self.stream_sizes:
            if len(self.stream_sizes) >= _MAX_PDF_RESOURCE_OBJECTS:
                raise _FileTooLarge
            size = len(stream.get_data())
            if size > _MAX_EXPANDED_MEMBER_BYTES:
                raise _FileTooLarge
            self.decoded_bytes += size
            if self.decoded_bytes > _MAX_EXPANDED_TOTAL_BYTES:
                raise _FileTooLarge
            self.stream_sizes[identity] = size
        return self.stream_sizes[identity]

    def _visit_resource(self, resource: Any, kind: str) -> bool:
        identity = (kind, id(resource))
        if identity in self.visited_resources:
            return False
        if len(self.visited_resources) >= _MAX_PDF_RESOURCE_OBJECTS:
            raise _FileTooLarge
        self.visited_resources.add(identity)
        return True

    def _check_font(self, font: Any) -> None:
        from pypdf.generic import StreamObject

        if not self._visit_resource(font, 'font'):
            return
        if '/ToUnicode' in font:
            mapping = font['/ToUnicode']
            if isinstance(mapping, StreamObject):
                self._stream_size(mapping)
        elif font.get('/Subtype') == '/Type1' and (descriptor := font.get('/FontDescriptor')):
            # pypdf can derive Type1 encodings from embedded font data instead.
            descriptor = descriptor.get_object()
            for key in ('/FontFile', '/FontFile3'):
                if key in descriptor:
                    stream = descriptor[key]
                    if isinstance(stream, StreamObject) and (key == '/FontFile' or stream.get('/Subtype') == '/Type1C'):
                        self._stream_size(stream)
                        break

    def _check_resources(self, owner: Any, contents: Any, depth: int) -> None:
        from pypdf.generic import ContentStream

        resources = owner.get_inherited('/Resources')
        if not resources:
            return
        if fonts := resources.get('/Font'):
            for font in fonts.get_object().values():
                self._check_font(font.get_object())
        if '/XObject' not in resources:
            return
        # Parse only content already checked against the per-page/stream limit.
        # Unused resources and raster image data are never decoded here.
        operations = ContentStream(contents, self.reader).operations
        for operands, operator in operations:
            if operator != b'Do' or not operands:
                continue
            form = resources['/XObject'][operands[0]]
            if form.get('/Subtype') == '/Image':
                continue
            if form.get('/Subtype') != '/Form':
                raise ValueError('Invalid PDF form')
            identity = id(form)
            if identity in self.active_forms:
                raise ValueError('Cyclic PDF form')
            if depth >= _MAX_PDF_RESOURCE_DEPTH:
                raise _FileTooLarge
            if self._visit_resource(form, 'form'):
                self._stream_size(form)
                self.active_forms.add(identity)
                try:
                    self._check_resources(form, form, depth + 1)
                finally:
                    self.active_forms.remove(identity)

    def check_page(self, page: Any) -> None:
        from pypdf.generic import ArrayObject, NullObject

        contents = page.get('/Contents')
        if contents is not None:
            contents = contents.get_object()
            if isinstance(contents, NullObject):
                contents = None
        if contents is None:
            # pypdf loads font mappings even on pages without a content stream.
            self._check_resources(page, None, depth=0)
            return
        if isinstance(contents, ArrayObject):
            if len(contents) > _MAX_PDF_RESOURCE_OBJECTS:
                raise _FileTooLarge
            page_bytes = 0
            for member in contents:
                stream = member.get_object()
                if isinstance(stream, NullObject):
                    continue
                # Reserve one separator byte per member before concatenation.
                page_bytes += self._stream_size(stream) + 1
                if page_bytes > _MAX_EXPANDED_MEMBER_BYTES:
                    raise _FileTooLarge
        else:
            self._stream_size(contents)
        self._check_resources(page, contents, depth=0)


def _extract_pdf(content: bytes, text: _BoundedText) -> None:
    from pypdf import PdfReader

    if not content.startswith(b'%PDF-'):
        raise ValueError('Invalid PDF')
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDF')
    if len(reader.pages) > _MAX_PDF_PAGES:
        raise _FileTooLarge
    budget = _PdfContentBudget(reader)
    visited_size = 0

    def count_text(fragment: str, *_: object) -> None:
        nonlocal visited_size
        visited_size += len(fragment)
        if visited_size > MAX_CHAT_ATTACHMENT_TEXT_CHARS:
            raise _FileTooLarge

    for page in reader.pages:
        budget.check_page(page)
        text.append((page.extract_text(visitor_text=count_text) or '').replace('\x0c', '\n'))


def _extract_docx(content: bytes, text: _BoundedText) -> None:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    _check_office_archive(content, 'word/document.xml')
    document = Document(io.BytesIO(content))
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            text.append(block.text)
        elif isinstance(block, Table):
            for row in block.rows:
                text.append_row(cell.text for cell in row.cells)


def _extract_xlsx(content: bytes, text: _BoundedText) -> None:
    from openpyxl import load_workbook

    _check_office_archive(content, 'xl/workbook.xml')
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
    cells = 0
    try:
        for sheet in workbook:
            if (sheet.max_row or 0) > _MAX_SHEET_ROWS or (sheet.max_column or 0) > _MAX_SHEET_COLUMNS:
                raise _FileTooLarge
            for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells += len(row)
                if index > _MAX_SHEET_ROWS or len(row) > _MAX_SHEET_COLUMNS or cells > _MAX_SHEET_CELLS:
                    raise _FileTooLarge
                text.append_row(row)
    finally:
        workbook.close()


def _resolve_attachment(managed: _ManagedFile, file_id: UUID, max_size_bytes: int) -> ResolvedPlatformAttachment:
    extension = Path(managed.file_name).suffix.lower()
    if extension not in CHAT_ATTACHMENT_EXTENSIONS or extension != managed.extension:
        raise ValueError('Unsupported document format')
    content = _read_owned_file(managed, max_size_bytes)
    text = _BoundedText()
    if extension == '.pdf':
        _extract_pdf(content, text)
    elif extension == '.docx':
        _extract_docx(content, text)
    elif extension == '.xlsx':
        _extract_xlsx(content, text)
    elif extension == '.csv':
        for row in csv.reader(io.StringIO(_decode_text(content)), strict=True):
            text.append_row(row)
    else:
        text.append(_decode_text(content))
    return ResolvedPlatformAttachment(
        file_id=file_id,
        file_name=managed.file_name,
        mime_type=_MIME_TYPES[extension],
        size_bytes=len(content),
        text=text.finish(),
    )
