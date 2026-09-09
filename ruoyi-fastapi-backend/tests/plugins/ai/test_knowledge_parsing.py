from importlib import import_module
from pathlib import Path

import pytest

from module_Knowledge.src.knowledge.indexing import process_file_to_markdown


def test_pdf_parser_reads_a_real_text_page(tmp_path: Path) -> None:
    pypdf = pytest.importorskip('pypdf')
    pdf_objects = import_module('pypdf.generic')

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = pdf_objects.DictionaryObject(
        {
            pdf_objects.NameObject('/Type'): pdf_objects.NameObject('/Font'),
            pdf_objects.NameObject('/Subtype'): pdf_objects.NameObject('/Type1'),
            pdf_objects.NameObject('/BaseFont'): pdf_objects.NameObject('/Helvetica'),
        }
    )
    page[pdf_objects.NameObject('/Resources')] = pdf_objects.DictionaryObject(
        {
            pdf_objects.NameObject('/Font'): pdf_objects.DictionaryObject(
                {pdf_objects.NameObject('/F1'): writer._add_object(font)}
            )
        }
    )
    stream = pdf_objects.DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 30 100 Td (Invoices need approval.) Tj ET')
    page[pdf_objects.NameObject('/Contents')] = writer._add_object(stream)
    path = tmp_path / 'guide.pdf'
    writer.write(path)
    assert 'Invoices need approval.' in process_file_to_markdown(path)


def test_docx_parser_keeps_paragraphs_and_table_content(tmp_path: Path) -> None:
    docx = pytest.importorskip('docx')
    document = docx.Document()
    document.add_paragraph('Invoice instructions')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = 'Receipt'
    table.cell(0, 1).text = 'Approval'
    path = tmp_path / 'guide.docx'
    document.save(path)
    text = process_file_to_markdown(path)
    assert 'Invoice instructions' in text
    assert 'Receipt | Approval' in text
