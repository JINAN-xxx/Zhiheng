# ruff: noqa: PLC0415
# Optional format parsers load only when their format is requested.
"""Selected file-to-Markdown flow from release/antd-minimal-loop f976247.

The original arbitrary-path/URL API is intentionally not exposed. The platform
passes only validated upload paths. Optional parsers load only for their format.
"""

import csv
import io
import json
from pathlib import Path

MAX_TEXT_BYTES = 2_000_000
MAX_PDF_PAGES = 500
SUPPORTED_EXTENSIONS = frozenset({'.txt', '.md', '.pdf', '.docx', '.csv', '.json', '.html', '.htm', '.xlsx'})


def process_file_to_markdown(file_path: Path) -> str:
    file_path = Path(file_path)
    extension = file_path.suffix.lower()
    if extension in {'.txt', '.md'}:
        text = file_path.read_text(encoding='utf-8-sig')
    elif extension == '.pdf':
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError('文档页数超过限制')
        text = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
    elif extension == '.docx':
        from docx import Document

        document = Document(file_path)
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        paragraphs.extend(
            ' | '.join(cell.text for cell in row.cells) for table in document.tables for row in table.rows
        )
        text = '\n'.join(paragraphs)
    elif extension == '.json':
        text = json.dumps(json.loads(file_path.read_text(encoding='utf-8')), ensure_ascii=False, indent=2)
    elif extension == '.csv':
        rows = csv.reader(io.StringIO(file_path.read_text(encoding='utf-8-sig')))
        text = '\n'.join(' | '.join(row) for row in rows)
    elif extension in {'.html', '.htm'}:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(file_path.read_text(encoding='utf-8'), 'html.parser')
        for element in soup(['script', 'style']):
            element.decompose()
        text = soup.get_text(separator='\n')
    elif extension == '.xlsx':
        from openpyxl import load_workbook

        workbook = load_workbook(file_path, read_only=True, data_only=True)
        try:
            text = '\n'.join(
                ' | '.join('' if value is None else str(value) for value in row)
                for sheet in workbook
                for row in sheet.iter_rows(values_only=True)
            )
        finally:
            workbook.close()
    else:
        raise ValueError('不支持的文件类型')
    text = text.strip()
    if not text or len(text.encode('utf-8')) > MAX_TEXT_BYTES:
        raise ValueError('文档为空或解析后超过限制')
    return text
