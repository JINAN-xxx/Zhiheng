"""Partial attachment text stays explicit and isolated from strict document parsing."""

import asyncio
import io
from unittest.mock import patch

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from zhiheng.modules.files.adapters.native import NativeParser
from zhiheng.modules.files.public import ATTACHMENT_PARSER_VERSION
from zhiheng.shared.errors import AppError


def mixed_pdf(*, with_text: bool = True) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    picture = DecodedStreamObject()
    picture.set_data(b'\xff\xff\xff' * 4)
    picture.update(
        {
            NameObject('/Type'): NameObject('/XObject'),
            NameObject('/Subtype'): NameObject('/Image'),
            NameObject('/Width'): NumberObject(2),
            NameObject('/Height'): NumberObject(2),
            NameObject('/BitsPerComponent'): NumberObject(8),
            NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
        }
    )
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/XObject'): DictionaryObject({NameObject('/Im1'): writer._add_object(picture)})}
    )
    draw = DecodedStreamObject()
    draw.set_data(b'q 100 0 0 100 0 0 cm /Im1 Do Q')
    page[NameObject('/Contents')] = writer._add_object(draw)
    if with_text:
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
        body = DecodedStreamObject()
        body.set_data(b'BT /F1 12 Tf 10 150 Td (Readable body) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(body)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


async def test_partial_pdf_keeps_body_and_identifies_unread_pages() -> None:
    result = await NativeParser(allow_partial_pdf=True).parse(mixed_pdf(), filename='book.pdf')
    assert 'Readable body' in result.text
    assert result.parser_version == ATTACHMENT_PARSER_VERSION
    assert result.metadata['image_only_page_numbers'] == '1'
    assert result.metadata['text_pages'] == 1
    assert '第 1 页' in str(result.metadata['extraction_warning'])
    assert '未识别' in str(result.metadata['extraction_warning'])


@pytest.mark.parametrize('partial', [False, True])
async def test_strict_mixed_pdf_and_fully_scanned_attachment_still_require_ocr(partial: bool) -> None:
    with pytest.raises(AppError) as caught:
        await NativeParser(allow_partial_pdf=partial).parse(mixed_pdf(with_text=not partial), filename='scan.pdf')
    assert caught.value.code == 'ocr_required'


async def test_cpu_limit_is_reported_as_timeout_instead_of_invalid_file() -> None:
    original = asyncio.create_subprocess_exec

    async def cpu_exit(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        return await original(str(args[0]), '-c', 'import os,signal;os.kill(os.getpid(),signal.SIGXCPU)', **kwargs)

    with (
        patch('zhiheng.modules.files.adapters.native.asyncio.create_subprocess_exec', cpu_exit),
        pytest.raises(AppError) as caught,
    ):
        await NativeParser().parse(b'body', filename='book.txt')
    assert caught.value.code == 'parse_timeout'
