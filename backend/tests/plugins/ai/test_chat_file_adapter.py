# ruff: noqa: PLC0415
import base64
import io
import zipfile
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.env import UploadConfig
from module_admin.entity.do.file_do import SysFileInfo
from plugins.ai.domain.chat_files import MAX_CHAT_ATTACHMENT_BYTES, MAX_CHAT_ATTACHMENT_TEXT_CHARS
from plugins.ai.infrastructure.chat_file_adapter import ChatFileAdapter
from plugins.ai.service.platform_chat_service import PlatformChatError


@pytest_asyncio.fixture
async def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, Any]]:
    public = tmp_path / 'public'
    private = tmp_path / 'private'
    public.mkdir()
    private.mkdir()
    monkeypatch.setattr(UploadConfig, 'UPLOAD_PATH', str(public))
    monkeypatch.setattr(UploadConfig, 'PRIVATE_UPLOAD_PATH', str(private))
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(SysFileInfo.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def upload(name: str, content: bytes, **overrides) -> tuple[UUID, Path]:
        file_id = uuid4()
        attributes = {
            'file_id': str(file_id),
            'original_name': name,
            'stored_name': name,
            'storage_key': f'upload/{file_id}/{name}',
            'storage_type': 'local',
            'access_type': 'private',
            'upload_user_id': 7,
            'owner_user_id': 7,
            'extension': Path(name).suffix.lstrip('.'),
            'content_type': 'application/octet-stream',
            'file_size': len(content),
            'file_hash': 'a' * 64,
            'status': 'active',
            'del_flag': '0',
            'expire_time': None,
        }
        attributes.update(overrides)
        root = public if attributes['access_type'] == 'public' else private
        path = root / f'upload/{file_id}/{name}'
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        async with factory() as session:
            session.add(SysFileInfo(**attributes))
            await session.commit()
        return file_id, path

    yield factory, upload
    await engine.dispose()


def adapter(files: Any) -> ChatFileAdapter:
    return ChatFileAdapter(files[0])


def image_bytes(image_format: str = 'PNG') -> bytes:
    output = io.BytesIO()
    Image.new('RGB', (2, 2), (255, 0, 0)).save(output, format=image_format)
    return output.getvalue()


def pdf_bytes(text: str = 'Invoices need approval.') -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        }
    )
    page[NameObject('/Resources')] = DictionaryObject(
        {
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)}),
        }
    )
    if text:
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 30 100 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def docx_bytes() -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph('发票审批说明')
    row = document.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text = '收据'
    row.cells[1].text = '审核通过'
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.title = '报销'
    workbook.active.append(['项目', '金额'])
    workbook.active.append(['差旅费', 120])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'image_format,mime',
    [
        ('PNG', 'image/png'),
        ('JPEG', 'image/jpeg'),
        ('GIF', 'image/gif'),
        ('WEBP', 'image/webp'),
    ],
)
@pytest.mark.parametrize('access_type', ['public', 'private'])
async def test_resolves_real_owned_images_and_sniffs_content(
    files: Any, image_format: str, mime: str, access_type: str
) -> None:
    content = image_bytes(image_format)
    file_id, _ = await files[1]('photo.png', content, access_type=access_type, content_type='text/plain')
    result = await adapter(files).resolve(7, file_id, max_size_bytes=1024)
    assert result.file_id == file_id
    assert result.mime_type == mime
    assert result.file_name == 'photo.png'
    assert result.size_bytes == len(content)
    assert result.data_url == f'data:{mime};base64,{base64.b64encode(content).decode()}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,code', [('resolve', 'CHAT_IMAGE_INVALID'), ('resolve_attachment', 'CHAT_ATTACHMENT_INVALID')]
)
@pytest.mark.parametrize(
    'overrides',
    [
        {'owner_user_id': 8},
        {'owner_user_id': None},
        {'status': 'recycled'},
        {'del_flag': '2'},
        {'expire_time': datetime.now() - timedelta(days=1)},
        {'storage_type': 's3'},
        {'access_type': 'unexpected'},
        {'storage_key': '../outside.txt'},
        {'storage_key': '/etc/passwd'},
        {'storage_key': 'https://example.com/private.txt'},
        {'storage_key': 'upload\\..\\outside.txt'},
    ],
)
async def test_rejects_unauthorized_and_unsafe_metadata(
    files: Any, overrides: dict[str, Any], method: str, code: str
) -> None:
    file_id, _ = await files[1]('notes.txt', b'private document', **overrides)
    with pytest.raises(PlatformChatError) as caught:
        await getattr(adapter(files), method)(7, file_id, max_size_bytes=1024)
    assert caught.value.code == code
    assert 'private document' not in str(caught.value)
    assert 'outside' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['resolve', 'resolve_attachment'])
@pytest.mark.parametrize('kind', ['missing', 'symlink_file', 'symlink_directory', 'directory'])
async def test_rejects_missing_nonregular_and_symlink_files(files: Any, tmp_path: Path, method: str, kind: str) -> None:
    file_id, path = await files[1]('notes.txt', b'private document')
    path.unlink()
    if kind == 'symlink_file':
        target = tmp_path / 'outside.txt'
        target.write_bytes(b'private document')
        path.symlink_to(target)
    elif kind == 'symlink_directory':
        target = tmp_path / 'outside'
        target.mkdir()
        (target / path.name).write_bytes(b'private document')
        path.parent.rmdir()
        path.parent.symlink_to(target, target_is_directory=True)
    elif kind == 'directory':
        path.mkdir()
    with pytest.raises(PlatformChatError):
        await getattr(adapter(files), method)(7, file_id, max_size_bytes=1024)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,code', [('resolve', 'CHAT_IMAGE_INVALID'), ('resolve_attachment', 'CHAT_ATTACHMENT_TOO_LARGE')]
)
@pytest.mark.parametrize('metadata_size', [1, 9999])
async def test_bounds_actual_and_declared_size(files: Any, method: str, code: str, metadata_size: int) -> None:
    file_id, _ = await files[1]('notes.txt', b'x' * 100, file_size=metadata_size)
    with pytest.raises(PlatformChatError) as caught:
        await getattr(adapter(files), method)(7, file_id, max_size_bytes=50)
    assert caught.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [b'not an image', image_bytes()[:-10], image_bytes('JPEG')[:-10]])
async def test_rejects_corrupt_or_fake_images(files: Any, content: bytes) -> None:
    file_id, _ = await files[1]('photo.png', content, content_type='image/png')
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve(7, file_id, max_size_bytes=1024)
    assert caught.value.code == 'CHAT_IMAGE_INVALID'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'extension,encoding',
    [
        ('txt', 'utf-8-sig'),
        ('txt', 'gb18030'),
        ('md', 'utf-8'),
        ('csv', 'utf-8-sig'),
        ('csv', 'gb18030'),
    ],
)
async def test_extracts_chinese_text_and_csv(files: Any, extension: str, encoding: str) -> None:
    content = '项目,金额\n差旅费,120' if extension == 'csv' else '# 报销说明\n请提供发票。'
    file_id, _ = await files[1](f'说明.{extension}', content.encode(encoding))
    result = await adapter(files).resolve_attachment(7, file_id, max_size_bytes=1024)
    assert result.file_name == f'说明.{extension}'
    assert result.file_id == file_id
    assert result.size_bytes == len(content.encode(encoding))
    assert ('差旅费 | 120' if extension == 'csv' else content) in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name,factory,expected,mime',
    [
        ('guide.pdf', pdf_bytes, ['Invoices need approval.'], 'application/pdf'),
        (
            'guide.docx',
            docx_bytes,
            ['发票审批说明', '收据 | 审核通过'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        ),
        (
            'guide.xlsx',
            xlsx_bytes,
            ['项目 | 金额', '差旅费 | 120'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        ),
    ],
)
async def test_extracts_real_documents(
    files: Any, name: str, factory: Callable[[], bytes], expected: list[str], mime: str
) -> None:
    file_id, _ = await files[1](name, factory(), content_type='image/png')
    result = await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert all(fragment in result.text for fragment in expected)
    assert result.mime_type == mime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name,content',
    [
        ('empty.txt', b' \n\t'),
        ('binary.txt', b'hello\x00world'),
        ('control.csv', b'hello\x1bworld'),
        ('binary.md', image_bytes()),
        ('fake.pdf', b'not pdf'),
        ('fake.docx', b'not docx'),
        ('fake.xlsx', b'not xlsx'),
        ('guide.exe', b'not supported'),
    ],
)
async def test_rejects_empty_binary_unsupported_and_false_extensions(files: Any, name: str, content: bytes) -> None:
    file_id, _ = await files[1](name, content)
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'


@pytest.mark.asyncio
async def test_rejects_pdf_without_extractable_text(files: Any) -> None:
    file_id, _ = await files[1]('scan.pdf', pdf_bytes(text=''))
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'


@pytest.mark.asyncio
async def test_rejects_text_over_context_limit_instead_of_truncating(files: Any) -> None:
    file_id, _ = await files[1]('large.txt', ('中' * (MAX_CHAT_ATTACHMENT_TEXT_CHARS + 1)).encode())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['bomb.docx', 'bomb.xlsx'])
async def test_rejects_zip_expansion_bombs_before_format_parser(files: Any, name: str) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', 'x' * (9 * 1024 * 1024))
    file_id, _ = await files[1](name, output.getvalue())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name,factory,xml_path',
    [
        ('guide.docx', docx_bytes, 'word/document.xml'),
        ('guide.xlsx', xlsx_bytes, 'xl/workbook.xml'),
    ],
)
async def test_rejects_xml_entities_without_exposing_expanded_content(
    files: Any, name: str, factory: Callable[[], bytes], xml_path: str
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(factory())) as source, zipfile.ZipFile(output, 'w') as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == xml_path:
                content = b'<!DOCTYPE root [<!ENTITY secret "PRIVATE-EXPANSION">]><root>&secret;</root>'
            target.writestr(item.filename, content)
    file_id, _ = await files[1](name, output.getvalue())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'
    assert 'PRIVATE-EXPANSION' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,code', [('resolve', 'CHAT_IMAGE_INVALID'), ('resolve_attachment', 'CHAT_ATTACHMENT_INVALID')]
)
async def test_unknown_file_has_same_sanitized_error_as_inaccessible_file(files: Any, method: str, code: str) -> None:
    with pytest.raises(PlatformChatError) as caught:
        await getattr(adapter(files), method)(7, uuid4(), max_size_bytes=1024)
    assert caught.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize('extension', ['csv', 'docx', 'xlsx'])
async def test_rejects_documents_containing_only_empty_table_cells(files: Any, extension: str) -> None:
    output = io.BytesIO()
    if extension == 'csv':
        output.write(b',,\n,,')
    elif extension == 'docx':
        from docx import Document

        document = Document()
        document.add_table(rows=1, cols=3)
        document.save(output)
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append([' ', ' ', ' '])
        workbook.save(output)
    file_id, _ = await files[1](f'empty.{extension}', output.getvalue())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'


@pytest.mark.asyncio
async def test_rejects_sparse_spreadsheet_before_iterating_a_million_rows(files: Any) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active['A1000000'] = 'last value'
    output = io.BytesIO()
    workbook.save(output)
    file_id, _ = await files[1]('sparse.xlsx', output.getvalue())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
@pytest.mark.parametrize('extension', ['csv', 'docx', 'pdf', 'xlsx'])
async def test_rejects_extracted_document_text_beyond_limit(files: Any, extension: str) -> None:
    output = io.BytesIO()
    text = 'x' * 1000
    if extension == 'csv':
        output.write(((text + '\n') * 41).encode())
    elif extension == 'docx':
        from docx import Document

        document = Document()
        for _ in range(41):
            document.add_paragraph(text)
        document.save(output)
    elif extension == 'pdf':
        output.write(pdf_bytes('x' * (MAX_CHAT_ATTACHMENT_TEXT_CHARS + 1)))
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        for _ in range(41):
            workbook.active.append([text])
        workbook.save(output)
    file_id, _ = await files[1](f'large.{extension}', output.getvalue())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
async def test_rejects_extension_metadata_mismatch(files: Any) -> None:
    file_id, _ = await files[1]('notes.txt', b'valid text', extension='pdf')
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=1024)
    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'


@pytest.mark.asyncio
async def test_rejects_password_protected_pdf(files: Any) -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes())))
    writer.encrypt('private-password')
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('encrypted.pdf', output.getvalue())
    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)
    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'
    assert 'private-password' not in str(caught.value)


@pytest.mark.asyncio
async def test_bounds_total_decoded_pdf_content_across_small_compressed_pages(
    files: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from plugins.ai.infrastructure import chat_file_adapter

    monkeypatch.setattr(chat_file_adapter, '_MAX_EXPANDED_MEMBER_BYTES', 2048)
    monkeypatch.setattr(chat_file_adapter, '_MAX_EXPANDED_TOTAL_BYTES', 2500)
    writer = PdfWriter()
    source = PdfReader(io.BytesIO(pdf_bytes('hello')))
    for _ in range(3):
        page = writer.add_page(source.pages[0])
        stream = DecodedStreamObject()
        stream.set_data(b'% ' + b'x' * 950 + b'\nBT /F1 12 Tf 30 100 Td (hello) Tj ET\n')
        page[NameObject('/Contents')] = writer._add_object(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('compressed-pages.pdf', output.getvalue())

    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
async def test_stops_decoding_pdf_contents_array_as_soon_as_page_budget_is_exceeded(
    files: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, EncodedStreamObject, NameObject

    from plugins.ai.infrastructure import chat_file_adapter

    monkeypatch.setattr(chat_file_adapter, '_MAX_EXPANDED_MEMBER_BYTES', 1500)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes('hello'))))
    streams = ArrayObject()
    for _ in range(2):
        stream = DecodedStreamObject()
        stream.set_data(b'% ' + b'x' * 950 + b'\nBT /F1 12 Tf 30 100 Td (hello) Tj ET\n')
        streams.append(writer._add_object(stream.flate_encode()))
    # A real unsupported filter proves the parser never decodes later members.
    unread_stream = EncodedStreamObject()
    unread_stream[NameObject('/Filter')] = NameObject('/UnsupportedFilter')
    unread_stream._data = b'never decode this stream'
    streams.append(writer._add_object(unread_stream))
    writer.pages[0][NameObject('/Contents')] = streams
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('compressed-array.pdf', output.getvalue())

    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
@pytest.mark.parametrize('resource_type', ['nested_forms', 'font_to_unicode', 'font_to_unicode_without_contents'])
async def test_pdf_resources_share_the_document_decoding_budget(
    files: Any, monkeypatch: pytest.MonkeyPatch, resource_type: str
) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from plugins.ai.infrastructure import chat_file_adapter

    monkeypatch.setattr(chat_file_adapter, '_MAX_EXPANDED_MEMBER_BYTES', 2048)
    monkeypatch.setattr(chat_file_adapter, '_MAX_EXPANDED_TOTAL_BYTES', 2500)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes('hello'))))
    page = writer.pages[0]
    if resource_type == 'nested_forms':
        resources = page['/Resources']
        operation = b'BT /F1 12 Tf 30 100 Td (hello) Tj ET\n'
        for name in ('/Nested2', '/Nested1', '/Nested0'):
            form = DecodedStreamObject()
            form.set_data(b'% ' + b'x' * 950 + b'\n' + operation)
            form[NameObject('/Subtype')] = NameObject('/Form')
            form[NameObject('/Resources')] = resources
            reference = writer._add_object(form.flate_encode())
            resources = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject(name): reference})})
            operation = name.encode() + b' Do\n'
        page[NameObject('/Resources')] = resources
        stream = DecodedStreamObject()
        stream.set_data(b'/Nested0 Do\n')
        page[NameObject('/Contents')] = writer._add_object(stream)
    else:
        fonts = page['/Resources']['/Font']
        for index in range(3):
            font = DictionaryObject(fonts['/F1'])
            mapping = DecodedStreamObject()
            mapping.set_data(b'% ' + b'x' * 950 + b'\n1 beginbfchar\n<68> <0068>\nendbfchar\n')
            font[NameObject('/ToUnicode')] = writer._add_object(mapping.flate_encode())
            fonts[NameObject(f'/Extra{index}')] = writer._add_object(font)
        if resource_type == 'font_to_unicode_without_contents':
            del page['/Contents']
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1](f'{resource_type}.pdf', output.getvalue())

    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
async def test_pdf_budget_does_not_decode_raster_images_or_unused_forms(files: Any) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, EncodedStreamObject, NameObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes('hello'))))
    page = writer.pages[0]
    xobjects = DictionaryObject()
    for name, subtype in [('/Raster', '/Image'), ('/Unused', '/Form')]:
        stream = EncodedStreamObject()
        stream[NameObject('/Subtype')] = NameObject(subtype)
        stream[NameObject('/Filter')] = NameObject('/UnsupportedFilter')
        stream._data = b'not text and must not be decoded'
        xobjects[NameObject(name)] = writer._add_object(stream)
    page['/Resources'][NameObject('/XObject')] = xobjects
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + b'/Raster Do\n')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('image-resources.pdf', output.getvalue())

    result = await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert 'hello' in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize('limit_name', ['_MAX_PDF_RESOURCE_DEPTH', '_MAX_PDF_RESOURCE_OBJECTS'])
async def test_pdf_form_traversal_has_depth_and_object_count_caps(
    files: Any, monkeypatch: pytest.MonkeyPatch, limit_name: str
) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from plugins.ai.infrastructure import chat_file_adapter

    monkeypatch.setattr(chat_file_adapter, limit_name, 1)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes('hello'))))
    page = writer.pages[0]
    resources = page['/Resources']
    operation = b'BT /F1 12 Tf 30 100 Td (hello) Tj ET\n'
    for name in ('/Inner', '/Outer'):
        form = DecodedStreamObject()
        form.set_data(operation)
        form[NameObject('/Subtype')] = NameObject('/Form')
        form[NameObject('/Resources')] = resources
        reference = writer._add_object(form.flate_encode())
        resources = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject(name): reference})})
        operation = name.encode() + b' Do\n'
    page[NameObject('/Resources')] = resources
    stream = DecodedStreamObject()
    stream.set_data(operation)
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('nested-caps.pdf', output.getvalue())

    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert caught.value.code == 'CHAT_ATTACHMENT_TOO_LARGE'


@pytest.mark.asyncio
async def test_pdf_shared_font_mapping_is_counted_once(files: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from plugins.ai.infrastructure import chat_file_adapter

    monkeypatch.setattr(chat_file_adapter, '_MAX_EXPANDED_TOTAL_BYTES', 1500)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes('hello'))))
    fonts = writer.pages[0]['/Resources']['/Font']
    mapping = DecodedStreamObject()
    mapping.set_data(b'% ' + b'x' * 950 + b'\n1 beginbfchar\n<68> <0068>\nendbfchar\n')
    shared_mapping = writer._add_object(mapping.flate_encode())
    for index in range(3):
        font = DictionaryObject(fonts['/F1'])
        font[NameObject('/ToUnicode')] = shared_mapping
        fonts[NameObject(f'/Extra{index}')] = writer._add_object(font)
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('shared-cmap.pdf', output.getvalue())

    result = await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert 'hello' in result.text


@pytest.mark.asyncio
async def test_pdf_cyclic_form_is_rejected(files: Any) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes('hello'))))
    page = writer.pages[0]
    form = DecodedStreamObject()
    form.set_data(b'/Loop Do\n')
    form[NameObject('/Subtype')] = NameObject('/Form')
    reference = writer._add_object(form)
    xobjects = DictionaryObject({NameObject('/Loop'): reference})
    form[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): xobjects})
    page['/Resources'][NameObject('/XObject')] = xobjects
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + b'/Loop Do\n')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    file_id, _ = await files[1]('cyclic-form.pdf', output.getvalue())

    with pytest.raises(PlatformChatError) as caught:
        await adapter(files).resolve_attachment(7, file_id, max_size_bytes=MAX_CHAT_ATTACHMENT_BYTES)

    assert caught.value.code == 'CHAT_ATTACHMENT_INVALID'
