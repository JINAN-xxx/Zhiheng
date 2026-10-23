"""Trusted installed worker entry; receives bytes on stdin, never paths or URLs."""

import base64
import csv
import io
import json
import resource
import sys
import warnings
import zipfile
from datetime import date, datetime, time
from html.parser import HTMLParser
from typing import NoReturn
from xml.etree.ElementTree import Element, fromstring
from xml.parsers.expat import ParserCreate

from openpyxl import load_workbook  # type: ignore[import-untyped]
from PIL import Image
from pypdf import PdfReader

from zhiheng.modules.knowledge.contracts import MAX_IMAGE_URL_BYTES, MAX_INPUT_BYTES, MAX_TEXT_BYTES, ParseResult

MAX_EXPANDED = 40 * 1024 * 1024
MAX_ZIP_MEMBERS = 2000
MAX_PIXELS = 20_000_000
MAX_FRAMES = 100
MAX_ROWS = 100000
MAX_COLUMNS = 1000
MAX_CELLS = 200000
MAX_SHEETS = 100
MAX_PAGES = 500


class DocumentError(Exception):
    pass


class HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {'script', 'style', 'noscript'}:
            self.hidden += 1
        if tag in {'p', 'br', 'div', 'tr', 'li', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in {'script', 'style', 'noscript'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def reject_xml_declaration(*_arguments: object) -> NoReturn:
    raise DocumentError('invalid_document')


def secure_xml(payload: bytes) -> Element:
    # Expat rejects declarations before any entity expansion, independent of optional SDK XML backends.
    parser = ParserCreate()
    parser.StartDoctypeDeclHandler = reject_xml_declaration
    parser.EntityDeclHandler = reject_xml_declaration
    parser.ExternalEntityRefHandler = reject_xml_declaration
    parser.Parse(payload, True)
    return fromstring(payload)


def package_payloads(content: bytes) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if len(archive.infolist()) > MAX_ZIP_MEMBERS:
            raise DocumentError('document_too_large')
        for info in archive.infolist():
            if info.flag_bits & 1 or info.filename.startswith('/') or '..' in info.filename.split('/'):
                raise DocumentError('invalid_document')
            with archive.open(info) as member:
                payload = member.read(min(MAX_EXPANDED - total, 8 * 1024 * 1024) + 1)
            total += len(payload)
            if total > MAX_EXPANDED or len(payload) > 8 * 1024 * 1024:
                raise DocumentError('document_too_large')
            upper = payload.upper()
            for token in ('<!DOCTYPE', '<!ENTITY'):
                for encoding in ('utf-8', 'utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be', 'cp037', 'cp500'):
                    if token.encode(encoding) in upper:
                        raise DocumentError('invalid_document')
            result[info.filename] = payload
    return result


def xml_parts(payloads: dict[str, bytes]) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    extensions = {'xml', 'rels', 'vml'}
    content_types = payloads.get('[Content_Types].xml')
    if content_types is not None:
        for part in secure_xml(content_types):
            content_type = part.get('ContentType', '').lower()
            if content_type.endswith(('+xml', '/xml')):
                name = part.get('PartName')
                extension = part.get('Extension')
                if name:
                    names.add(name.lstrip('/'))
                if extension:
                    extensions.add(extension.lower())
    return names, extensions


def package_xml(content: bytes) -> dict[str, Element]:
    payloads = package_payloads(content)
    names, extensions = xml_parts(payloads)
    result: dict[str, Element] = {}
    for name, payload in payloads.items():
        looks_xml = payload.lstrip(b'\xef\xbb\xbf\xff\xfe\x00 \t\r\n').startswith(b'<')
        if name in names or name.rpartition('.')[2].lower() in extensions or looks_xml:
            result[name] = secure_xml(payload)
    return result


def encode_safe_image(image: Image.Image) -> str:
    for quality in (80, 65, 50):
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=quality)
        data = 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
        if len(data) <= MAX_IMAGE_URL_BYTES:
            return data
    raise DocumentError('document_too_large')


def image_result(content: bytes) -> ParseResult:
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with Image.open(io.BytesIO(content)) as check:
        if (
            check.format not in {'PNG', 'JPEG', 'GIF', 'WEBP'}
            or check.width * check.height > MAX_PIXELS
            or getattr(check, 'n_frames', 1) > MAX_FRAMES
        ):
            raise DocumentError('document_too_large')
        metadata = {
            'format': check.format,
            'width': check.width,
            'height': check.height,
            'frames': getattr(check, 'n_frames', 1),
        }
        check.verify()
    with Image.open(io.BytesIO(content)) as image:
        image.seek(0)
        image.thumbnail((1536, 1536))
        with image.convert('RGB') as safe:
            data = encode_safe_image(safe)
            metadata.update(normalized_width=safe.width, normalized_height=safe.height)
    return ParseResult(text='[Image attachment]', metadata=metadata, image_data=data)


def pdf_result(content: bytes) -> tuple[str, dict[str, str | int]]:
    if not content.startswith(b'%PDF-'):
        raise DocumentError('invalid_document')
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise DocumentError('encrypted_document')
    if len(reader.pages) > MAX_PAGES:
        raise DocumentError('document_too_large')
    parts = []
    total = 0
    for page in reader.pages:
        part = page.extract_text() or ''
        total += len(part.encode())
        if total > MAX_TEXT_BYTES:
            raise DocumentError('document_too_large')
        parts.append(part)
    text = '\n'.join(parts)
    if not text.strip():
        raise DocumentError('ocr_required')
    return text, {'pages': len(reader.pages)}


def check_sheet_bounds(documents: dict[str, Element]) -> int:
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    cells = 0
    for root in documents.values():
        if root.tag == ns + 'worksheet':
            for row in root.iter(ns + 'row'):
                if int(row.get('r', '1')) > MAX_ROWS:
                    raise DocumentError('document_too_large')
                for _ in row.iter(ns + 'c'):
                    cells += 1
                    if cells > MAX_CELLS:
                        raise DocumentError('document_too_large')
    return cells


def xlsx_result(content: bytes, documents: dict[str, Element]) -> tuple[str, dict[str, str | int]]:
    cells = check_sheet_bounds(documents)
    book = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        if len(book.worksheets) > MAX_SHEETS:
            raise DocumentError('document_too_large')
        lines = []
        total = 0
        has_cell_text = False
        for sheet in book.worksheets:
            sheet.reset_dimensions()
            lines.append(f'[Sheet: {sheet.title}]')
            for index, row_values in enumerate(sheet.iter_rows(values_only=True)):
                if index >= MAX_ROWS or len(row_values) > MAX_COLUMNS:
                    raise DocumentError('document_too_large')
                values = []
                for cell in row_values:
                    value: object = cell
                    values.append(
                        value.isoformat()
                        if isinstance(value, (datetime, date, time))
                        else ''
                        if value is None
                        else str(value)
                    )
                has_cell_text = has_cell_text or any(value.strip() for value in values)
                line = '\t'.join(values)
                total += len(line.encode())
                if total > MAX_TEXT_BYTES:
                    raise DocumentError('document_too_large')
                lines.append(line)
        if not has_cell_text:
            raise DocumentError('empty_document')
        return '\n'.join(lines), {'sheets': len(book.worksheets), 'cells': cells}
    finally:
        book.close()


def text_result(content: bytes, extension: str) -> str:
    text = content.decode('utf-8-sig')
    if '\x00' in text:
        raise DocumentError('invalid_document')
    if extension == 'json':
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    if extension == 'csv':
        csv.field_size_limit(1_000_000)
        lines = []
        for index, row in enumerate(csv.reader(io.StringIO(text))):
            if index >= MAX_ROWS or len(row) > MAX_COLUMNS:
                raise DocumentError('document_too_large')
            lines.append('\t'.join(row))
        return '\n'.join(lines)
    if extension in {'html', 'htm'}:
        html = HtmlText()
        html.feed(text)
        html.close()
        return ''.join(html.parts)
    return text


def parse(content: bytes, filename: str) -> ParseResult:
    extension = filename.rpartition('.')[2].lower()
    metadata: dict[str, str | int] = {'format': extension}
    if extension in {'png', 'jpg', 'jpeg', 'gif', 'webp'}:
        return image_result(content)
    if extension in {'txt', 'md', 'json', 'csv', 'html', 'htm'}:
        text = text_result(content, extension)
    elif extension == 'pdf':
        text, extra = pdf_result(content)
        metadata.update(extra)
    elif extension in {'docx', 'xlsx'}:
        documents = package_xml(content)
        if extension == 'docx':
            root = documents['word/document.xml']
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(t.text or '' for t in p.iter(ns + 't')) for p in root.iter(ns + 'p'))
        else:
            text, extra = xlsx_result(content, documents)
            metadata.update(extra)
    else:
        raise DocumentError('unsupported_format')
    text = text.strip()
    if not text or len(text.encode()) > MAX_TEXT_BYTES:
        raise DocumentError('document_too_large' if text else 'empty_document')
    return ParseResult(text=text, metadata=metadata)


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    reply: dict[str, object]
    try:
        filename = sys.stdin.buffer.readline(1025).decode().rstrip('\n')
        content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if not content or len(content) > MAX_INPUT_BYTES:
            raise DocumentError('document_too_large')
        result = parse(content, filename)
        reply = {'result': result.model_dump()}
    except DocumentError as error:
        reply = {'error': str(error)}
    except Exception:
        reply = {'error': 'invalid_document'}
    sys.stdout.write(json.dumps(reply, ensure_ascii=False))


if __name__ == '__main__':
    main()
