"""Fixed, bounded office readers; generated code belongs exclusively to the sandbox."""

# ruff: noqa: PLC0415 -- Parsing SDKs load only inside the trusted reader child.

import csv
import io
import itertools
import json
import math
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable, Iterator, Sequence
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath
from typing import NoReturn, TypeVar
from xml.etree.ElementTree import Element, fromstring
from xml.parsers.expat import ParserCreate

from zhiheng.modules.files.parsing_contracts import MAX_INPUT_BYTES
from zhiheng.modules.files.workspace_contracts import WorkspaceFileView
from zhiheng.shared.csv_text import decode_csv_text
from zhiheng.shared.errors import AppError

MAX_ZIP_MEMBERS = 2000
MAX_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100000
MAX_COLUMNS = 50
MAX_SHEETS = 100
MAX_PAGES = 500
MAX_PAGE_LIMIT = 3
MAX_DOCUMENT_LIMIT = 60
MAX_SHEET_LIMIT = 100
MAX_CELL_BYTES = 512
MAX_ITEM_BYTES = 3000
MAX_RESULT_BYTES = 32768
MAX_FILENAME_BYTES = 1024
RESULT_METADATA_BYTES = 1024
WORKER_TIMEOUT_SECONDS = 20
TEXT_EXTENSIONS = frozenset({'.txt', '.md', '.csv', '.log', '.json', '.yaml', '.yml'})
WORD_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
T = TypeVar('T')


def _bounded_text(value: str, size: int) -> str:
    return value.encode('utf-8')[:size].decode('utf-8', errors='ignore')


def _secure_xml(content: bytes) -> Element:
    def reject(*_args: object) -> NoReturn:
        raise ValueError('XML declarations are not allowed')

    parser = ParserCreate()
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.Parse(content, True)
    return fromstring(content)


def _package(content: bytes) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_MEMBERS or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise AppError('document_too_large', '文档展开内容超过限制，请拆分后处理', 422)
        total = 0
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                entry.flag_bits & 1
                or path.is_absolute()
                or '..' in path.parts
                or '\\' in entry.filename
                or entry.filename in result
            ):
                raise ValueError('Invalid office archive')
            with archive.open(entry) as source:
                payload = source.read(min(MAX_MEMBER_BYTES, MAX_EXPANDED_BYTES - total) + 1)
            total += len(payload)
            if len(payload) > MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES:
                raise AppError('document_too_large', '文档展开内容超过限制，请拆分后处理', 422)
            if entry.filename.endswith('.xml') or entry.filename.endswith('.rels'):
                _secure_xml(payload)
            result[entry.filename] = payload
    return result


def _cell(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    result = _bounded_text(str(value), MAX_CELL_BYTES)
    while len(json.dumps(result, ensure_ascii=False).encode()) > MAX_CELL_BYTES:
        result = result[: len(result) // 2]
    return result


def _csv(content: bytes) -> Iterator[list[str]]:
    text = decode_csv_text(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    yield from csv.reader(io.StringIO(text, newline=''), dialect)


def _checked(content: bytes, filename: str, operation: Callable[[], T]) -> T:
    try:
        valid_name = '\x00' not in filename and len(filename.encode()) <= MAX_FILENAME_BYTES
    except UnicodeError:
        valid_name = False
    if not content or len(content) > MAX_INPUT_BYTES or not valid_name:
        raise AppError('invalid_parse_input', '文件为空、过大或名称无效', 422)
    try:
        return operation()
    except AppError:
        raise
    except Exception as error:
        raise AppError('file_reader_failed', '文件内容无法读取，请检查格式和编码', 422) from error


def _fit_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in items:
        if len(json.dumps([*result, item], ensure_ascii=False).encode()) > MAX_RESULT_BYTES - RESULT_METADATA_BYTES:
            break
        result.append(item)
    return result


class _OfficeReader:
    def inspect(self, content: bytes, filename: str) -> WorkspaceFileView:
        return _checked(content, filename, lambda: self._inspect(content, filename))

    def _inspect(self, content: bytes, filename: str) -> WorkspaceFileView:
        extension = PurePosixPath(filename).suffix.lower()
        result: WorkspaceFileView = {'format': extension.lstrip('.'), 'bytes': len(content)}
        if extension in {'.xlsx', '.xlsm'}:
            result.update(self._workbook_structure(content))
        elif extension == '.csv':
            rows = list(itertools.islice(_csv(content), 6))
            sample: list[list[object]] = []
            for row in rows:
                converted = [_cell(value) for value in row[:MAX_COLUMNS]]
                if (
                    len(json.dumps([*sample, converted], ensure_ascii=False).encode())
                    > MAX_RESULT_BYTES - RESULT_METADATA_BYTES
                ):
                    break
                sample.append(converted)
            result['sample'] = sample
            result['columns'] = max((len(row) for row in rows), default=0)
        elif extension == '.docx':
            blocks = self._word_blocks(content)
            result.update(blocks=len(blocks), sample=_bounded_text('\n'.join(blocks[:5]), MAX_ITEM_BYTES))
        elif extension == '.pdf':
            from pypdf import PdfReader

            document = PdfReader(io.BytesIO(content))
            if document.is_encrypted:
                raise AppError('encrypted_document', 'PDF 已加密，请提供无密码副本', 422)
            if len(document.pages) > MAX_PAGES:
                raise AppError('document_too_large', 'PDF 页数超过限制，请拆分后处理', 422)
            result.update(pages=len(document.pages), note='可提取文字；没有可提取文字的页面需要另行 OCR。')
        elif extension in TEXT_EXTENSIONS:
            result['sample'] = _bounded_text(content.decode('utf-8-sig'), MAX_ITEM_BYTES)
        else:
            result['note'] = '已保留原文件，此格式暂不提供结构读取。'
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
            raise AppError('document_too_large', '文件结构信息超过读取限制', 422)
        return result

    @staticmethod
    def _workbook_structure(content: bytes) -> WorkspaceFileView:
        _package(content)
        from openpyxl import load_workbook  # type: ignore[import-untyped]

        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            if len(workbook.worksheets) > MAX_SHEETS:
                raise AppError('document_too_large', '工作表数量超过限制', 422)
            return {
                'sheets': [
                    {'name': sheet.title, 'rows': sheet.max_row, 'columns': sheet.max_column}
                    for sheet in workbook.worksheets
                ],
                'note': '公式按原文读取，未重新计算。',
            }
        finally:
            workbook.close()

    def read_document(self, content: bytes, filename: str, start: int = 1, limit: int = 40) -> WorkspaceFileView:
        if start < 1 or start > MAX_ROWS or not 1 <= limit <= MAX_DOCUMENT_LIMIT:
            raise AppError('invalid_file_page', '文档分页参数无效', 422)
        return _checked(content, filename, lambda: self._document(content, filename, start, limit))

    def _document(self, content: bytes, filename: str, start: int, limit: int) -> WorkspaceFileView:
        extension = PurePosixPath(filename).suffix.lower()
        unit = 'line'
        if extension == '.docx':
            blocks = self._word_blocks(content)
            total, selected, unit = len(blocks), blocks[start - 1 : start - 1 + limit], 'block'
        elif extension == '.pdf':
            from pypdf import PdfReader

            document = PdfReader(io.BytesIO(content))
            if document.is_encrypted:
                raise AppError('encrypted_document', 'PDF 已加密，请提供无密码副本', 422)
            if len(document.pages) > MAX_PAGES:
                raise AppError('document_too_large', 'PDF 页数超过限制，请拆分后处理', 422)
            selected = []
            for page in document.pages[start - 1 : start - 1 + min(limit, MAX_PAGE_LIMIT)]:
                stream = page.get_contents()
                if stream is not None and len(stream.get_data()) > MAX_MEMBER_BYTES:
                    raise AppError('document_too_large', 'PDF 页面内容流超过限制', 422)
                selected.append(page.extract_text() or '')
            total, unit = len(document.pages), 'page'
        elif extension in TEXT_EXTENSIONS:
            blocks = content.decode('utf-8-sig').splitlines()
            total, selected = len(blocks), blocks[start - 1 : start - 1 + limit]
        else:
            raise AppError('unsupported_format', '此文件请使用表格读取工具', 422)
        items = _fit_items(
            [
                {'number': start + index, 'text': _bounded_text(text, MAX_ITEM_BYTES)}
                for index, text in enumerate(selected)
            ]
        )
        return {
            'unit': unit,
            'total': total,
            'items': items,
            'next': start + len(items) if items and start + len(items) <= total else None,
            'note': '返回指定页段或文本块；截断长内容时可继续分页读取。',
        }

    @staticmethod
    def _word_blocks(content: bytes) -> list[str]:
        package = _package(content)
        payload = package.get('word/document.xml')
        if payload is None:
            raise ValueError('Missing Word document')
        document = _secure_xml(payload)
        body = document.find(WORD_NS + 'body')
        if body is None:
            return []
        result = []
        for element in body:
            if element.tag == WORD_NS + 'p':
                result.append(''.join(part.text or '' for part in element.iter(WORD_NS + 't')))
            elif element.tag == WORD_NS + 'tbl':
                result.append(
                    '\n'.join(
                        ' | '.join(
                            ''.join(part.text or '' for part in cell.iter(WORD_NS + 't'))
                            for cell in row.findall(WORD_NS + 'tc')
                        )
                        for row in element.findall(WORD_NS + 'tr')
                    )
                )
        return result

    def read_sheet(
        self, content: bytes, filename: str, sheet: str = '', start_row: int = 1, limit: int = 40
    ) -> WorkspaceFileView:
        if start_row < 1 or start_row > MAX_ROWS or not 1 <= limit <= MAX_SHEET_LIMIT:
            raise AppError('invalid_file_page', '工作表分页参数无效', 422)
        return _checked(content, filename, lambda: self._sheet(content, filename, sheet, start_row, limit))

    def _sheet(self, content: bytes, filename: str, sheet: str, start_row: int, limit: int) -> WorkspaceFileView:
        extension = PurePosixPath(filename).suffix.lower()
        total: int | None = None
        selected: list[Sequence[object]]
        if extension == '.csv':
            name = 'CSV'
            selected = list(itertools.islice(_csv(content), start_row - 1, start_row + limit))
        elif extension in {'.xlsx', '.xlsm'}:
            _package(content)
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
            try:
                if sheet and sheet not in workbook.sheetnames:
                    raise AppError('sheet_not_found', '工作表不存在，请先检查文件结构', 422)
                target = workbook[sheet] if sheet else workbook.worksheets[0]
                name, total = target.title, target.max_row
                if total is not None and total > MAX_ROWS:
                    raise AppError('document_too_large', '工作表行数超过限制，请拆分后处理', 422)
                selected = (
                    list(
                        target.iter_rows(
                            min_row=start_row,
                            max_row=min(start_row + limit, total or start_row),
                            max_col=min(target.max_column or 1, MAX_COLUMNS),
                            values_only=True,
                        )
                    )
                    if total is None or start_row <= total
                    else []
                )
            finally:
                workbook.close()
        else:
            raise AppError('unsupported_format', '此文件不是 CSV 或 Excel 工作表', 422)
        rows: list[list[object]] = []
        used = 0
        for row in selected[:limit]:
            converted = [_cell(value) for value in row[:MAX_COLUMNS]]
            size = len(json.dumps(converted, ensure_ascii=False).encode())
            if rows and used + size > MAX_RESULT_BYTES - RESULT_METADATA_BYTES:
                break
            rows.append(converted)
            used += size
        more = len(selected) > len(rows) or (total is not None and start_row + len(rows) <= total)
        return {
            'sheet': name,
            'start_row': start_row,
            'rows': rows,
            'total_rows': total,
            'next_row': start_row + len(rows) if rows and more else None,
            'note': '最多 50 列，长单元格返回预览；公式保留原文，未重新计算。',
        }


class WorkspaceFileReader:
    """Run the fixed parser in a bounded subprocess, using the same isolation pattern as NativeParser."""

    def _invoke(self, operation: str, content: bytes, filename: str, **arguments: object) -> WorkspaceFileView:
        def perform() -> WorkspaceFileView:
            request = json.dumps({'operation': operation, 'filename': filename, **arguments}).encode() + b'\n' + content
            try:
                result = subprocess.run(
                    [sys.executable, '-m', 'zhiheng.modules.files.adapters.workspace_worker'],
                    input=request,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=WORKER_TIMEOUT_SECONDS,
                    check=False,
                    env={
                        'PATH': os.environ.get('PATH', os.defpath),
                        'LANG': 'C.UTF-8',
                        'LC_ALL': 'C.UTF-8',
                        'PYTHONDONTWRITEBYTECODE': '1',
                        'PYTHONPATH': str(Path(__file__).resolve().parents[4])
                        + os.pathsep
                        + os.environ.get('PYTHONPATH', ''),
                        'OPENBLAS_NUM_THREADS': '1',
                        'OMP_NUM_THREADS': '1',
                    },
                )
            except subprocess.TimeoutExpired:
                raise AppError('parse_timeout', '文件读取超时，请拆分后处理', 422) from None
            if result.returncode != 0 or len(result.stdout) > MAX_RESULT_BYTES + RESULT_METADATA_BYTES:
                raise AppError('parse_resource_limit', '文件读取超过资源限制', 422)
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise ValueError('Invalid reader reply')
            error = payload.get('error')
            if isinstance(error, dict):
                raise AppError(
                    str(error.get('code', 'file_reader_failed')), str(error.get('message', '文件内容无法读取')), 422
                )
            data = payload.get('data')
            if not isinstance(data, dict):
                raise ValueError('Invalid reader data')
            return data

        return _checked(content, filename, perform)

    def inspect(self, content: bytes, filename: str) -> WorkspaceFileView:
        return self._invoke('inspect', content, filename)

    def read_document(self, content: bytes, filename: str, start: int = 1, limit: int = 40) -> WorkspaceFileView:
        return self._invoke('read_document', content, filename, start=start, limit=limit)

    def read_sheet(
        self, content: bytes, filename: str, sheet: str = '', start_row: int = 1, limit: int = 40
    ) -> WorkspaceFileView:
        return self._invoke('read_sheet', content, filename, sheet=sheet, start_row=start_row, limit=limit)
