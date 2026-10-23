from io import BytesIO
from zipfile import ZipFile

import pytest


async def test_real_native_formats() -> None:
    from zhiheng.modules.knowledge.public import NativeParser

    parser = NativeParser()
    for name, data, expected in [
        ('a.txt', b'hello', 'hello'),
        ('a.md', b'# heading', 'heading'),
        ('a.json', b'{"a":1}', '"a"'),
        ('a.csv', b'a,b\n1,2', 'a'),
        ('a.html', b'<script>bad</script><p>good</p>', 'good'),
    ]:
        result = await parser.parse(data, filename=name)
        assert expected in result.text
        assert 'bad' not in result.text
    docx = BytesIO()
    with ZipFile(docx, 'w') as z:
        z.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>document</w:t></w:r></w:p></w:body></w:document>',
        )
    assert 'document' in (await parser.parse(docx.getvalue(), filename='a.docx')).text


async def test_real_bad_zip_and_scanned_pdf() -> None:
    from pypdf import PdfWriter

    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    parser = NativeParser()
    with pytest.raises(AppError):
        await parser.parse(b'PK\x03\x04broken', filename='bad.docx')
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.write(stream)
    with pytest.raises(AppError, match='OCR'):
        await parser.parse(stream.getvalue(), filename='scan.pdf')


async def test_xlsx_rich_dates_and_docx_tables() -> None:
    from datetime import datetime

    from openpyxl import Workbook

    from zhiheng.modules.knowledge.public import NativeParser

    book = Workbook()
    sheet = book.active
    sheet.append(['name', datetime(2026, 1, 2), 42])
    sheet['B1'].number_format = 'yyyy-mm-dd'
    other = book.create_sheet('Other')
    other.append(['second sheet'])
    stream = BytesIO()
    book.save(stream)
    book.close()
    result = await NativeParser().parse(stream.getvalue(), filename='table.xlsx')
    assert '2026-01-02' in result.text and 'second sheet' in result.text


async def test_parser_timeout_and_cancellation_drain() -> None:
    import asyncio

    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError) as error:
        await NativeParser(timeout_seconds=0.00001).parse(b'hello', filename='a.txt')
    assert error.value.code == 'parse_timeout'
    task = asyncio.create_task(NativeParser().parse(b'hello', filename='a.txt'))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_zip_entity_and_actual_expansion_bounds() -> None:
    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    for body in [b'<!DOCTYPE a [<!ENTITY x "hello">]><a>&x;</a>', b'a' * (8 * 1024 * 1024 + 1)]:
        stream = BytesIO()
        with ZipFile(stream, 'w') as z:
            z.writestr('word/document.xml', body)
        with pytest.raises(AppError):
            await NativeParser().parse(stream.getvalue(), filename='bad.docx')


async def test_real_text_pdf_and_password_protection() -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    font = DictionaryObject(
        {
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        }
    )
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 100 Td (actual PDF text) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    assert 'actual PDF text' in (await NativeParser().parse(output.getvalue(), filename='text.pdf')).text
    writer.encrypt('password')
    output = BytesIO()
    writer.write(output)
    with pytest.raises(AppError) as error:
        await NativeParser().parse(output.getvalue(), filename='password.pdf')
    assert error.value.code == 'encrypted_document'


@pytest.mark.parametrize('character', [b'"', b'\x01'])
async def test_legal_text_limit_survives_json_wire_escaping(character: bytes) -> None:
    from zhiheng.modules.knowledge.public import NativeParser

    content = character * 1_999_999
    result = await NativeParser().parse(content, filename='quoted.txt')
    assert result.text.encode() == content


async def test_relationship_xml_entities_are_rejected() -> None:
    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>valid text</w:t></w:r></w:p></w:document>',
        )
        archive.writestr('_rels/.rels', '<!DOCTYPE x [<!ENTITY bad "bad">]><Relationships>&bad;</Relationships>')
    with pytest.raises(AppError):
        await NativeParser().parse(stream.getvalue(), filename='relationships.docx')


async def test_noise_image_normalizes_within_model_image_cap() -> None:
    import random

    from PIL import Image

    from zhiheng.modules.knowledge.public import NativeParser

    image = Image.frombytes('RGB', (1536, 1536), random.Random(0).randbytes(1536 * 1536 * 3))
    output = BytesIO()
    image.save(output, format='PNG')
    image.close()
    result = await NativeParser().parse(output.getvalue(), filename='noise.png')
    assert result.image_data is not None and len(result.image_data) <= 2_000_000


def opaque_workbook(*, dtd: bool, rows: int = 1, columns: int = 1) -> bytes:
    from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

    from openpyxl import Workbook
    from openpyxl.utils.cell import get_column_letter

    output = BytesIO()
    book = Workbook()
    book.active['A1'] = 'placeholder'
    book.save(output)
    book.close()
    with ZipFile(BytesIO(output.getvalue())) as original:
        parts = {name: original.read(name) for name in original.namelist()}
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    worksheet = Element('{' + ns + '}worksheet')
    sheet_data = SubElement(worksheet, '{' + ns + '}sheetData')
    for row_index in range(1, rows + 1):
        row = SubElement(sheet_data, '{' + ns + '}row', r=str(row_index))
        for column in range(1, columns + 1):
            cell = SubElement(row, '{' + ns + '}c', r=f'{get_column_letter(column)}{row_index}', t='s' if dtd else 'n')
            SubElement(cell, '{' + ns + '}v').text = '0'
    parts.pop('xl/worksheets/sheet1.xml')
    parts['xl/opaque-sheet.bin'] = tostring(worksheet)
    relations = fromstring(parts['xl/_rels/workbook.xml.rels'])
    for relation in relations:
        if relation.get('Type', '').endswith('/worksheet'):
            relation.set('Target', '/xl/opaque-sheet.bin')
    parts['xl/_rels/workbook.xml.rels'] = tostring(relations)
    content_types = fromstring(parts['[Content_Types].xml'])
    types_ns = 'http://schemas.openxmlformats.org/package/2006/content-types'
    for entry in content_types:
        if entry.get('PartName') == '/xl/worksheets/sheet1.xml':
            entry.set('PartName', '/xl/opaque-sheet.bin')
    if dtd:
        SubElement(
            content_types,
            '{' + types_ns + '}Override',
            PartName='/xl/sharedStrings.bin',
            ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml',
        )
        parts['xl/sharedStrings.bin'] = (
            f'<!DOCTYPE sst [<!ENTITY bad "EXPANDED_ENTITY">]><sst xmlns="{ns}" count="1" uniqueCount="1"><si><t>&bad;</t></si></sst>'.encode()
        )
    parts['[Content_Types].xml'] = tostring(content_types)
    result = BytesIO()
    from zipfile import ZIP_DEFLATED

    with ZipFile(result, 'w', compression=ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return result.getvalue()


async def test_opaque_xml_part_rejects_entities_without_optional_sdk_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    monkeypatch.setenv('OPENPYXL_DEFUSEDXML', 'False')
    monkeypatch.setenv('OPENPYXL_LXML', 'False')
    with pytest.raises(AppError):
        await NativeParser().parse(opaque_workbook(dtd=True), filename='opaque.xlsx')


async def test_opaque_worksheet_actual_cells_obey_limit() -> None:
    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError) as error:
        await NativeParser().parse(opaque_workbook(dtd=False, rows=201, columns=1000), filename='opaque.xlsx')
    assert error.value.code == 'document_too_large'


async def test_empty_workbook_requires_real_cell_text_but_keeps_zero_and_false() -> None:
    from openpyxl import Workbook

    from zhiheng.modules.knowledge.public import NativeParser
    from zhiheng.shared.errors import AppError

    book = Workbook()
    output = BytesIO()
    book.save(output)
    with pytest.raises(AppError) as error:
        await NativeParser().parse(output.getvalue(), filename='empty.xlsx')
    assert error.value.code == 'empty_document'
    book.active.append([0, False])
    output = BytesIO()
    book.save(output)
    book.close()
    result = await NativeParser().parse(output.getvalue(), filename='nonempty.xlsx')
    assert '0' in result.text and 'False' in result.text
