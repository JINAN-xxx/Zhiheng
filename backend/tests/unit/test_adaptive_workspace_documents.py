import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from xml.sax.saxutils import escape

import pytest

from tests.unit.test_workspace_tools import make_tools
from zhiheng.application.work.document_budget import document_allowance, tool_result_tokens
from zhiheng.modules.files.adapters import workspace as reader_module
from zhiheng.modules.files.adapters.workspace import WorkspaceFileReader
from zhiheng.modules.models.public import (
    AgentModelMessage,
    ModelBinding,
    ModelMessage,
    estimate_messages,
    estimate_tool_schemas,
)
from zhiheng.shared.errors import AppError


def word_blocks(blocks: list[str], *, table: bool = False) -> bytes:
    output = io.BytesIO()
    paragraphs = ['<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>' for text in blocks]
    if table:
        paragraphs[0] = '<w:tbl><w:tr><w:tc>' + paragraphs[0] + '</w:tc></w:tr></w:tbl>'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + ''.join(paragraphs)
            + '</w:body></w:document>',
        )
    return output.getvalue()


def test_default_read_covers_164_blocks_and_inspection_reuses_the_isolated_parse() -> None:
    blocks = [f'第{index + 1}段。' + '内容与验证依据。' * 10 for index in range(164)]
    content = word_blocks(blocks)
    reader = WorkspaceFileReader()
    with patch.object(reader, '_invoke', wraps=reader._invoke) as invoked:
        assert reader.inspect(content, 'report.docx')['blocks'] == 164
        page = reader.read_document(content, 'report.docx')
        assert invoked.call_count == 1
    assert page['complete'] is True and page['has_more'] is False and page['next'] is None
    assert [item['text'] for item in page['items']] == blocks
    assert len(json.dumps(page, ensure_ascii=False)) > 10000


@pytest.mark.parametrize('table', [False, True])
def test_long_paragraph_or_table_resumes_with_no_missing_or_duplicate_characters(table: bool) -> None:
    original = 'HEAD' + '汉字🚦abc' * 3000 + 'TAIL'
    content = word_blocks([original, '最后一段'], table=table)
    reader = WorkspaceFileReader()
    start, offset, seen = 1, 0, []
    with patch.object(reader, '_invoke', wraps=reader._invoke) as invoked:
        for _ in range(100):
            page = reader.read_document(content, 'long.docx', start, offset=offset, max_characters=511)
            seen.extend(item['text'] for item in page['items'] if item['number'] == 1)
            if not page['has_more']:
                break
            assert (page['next'], page['next_offset']) != (start, offset)
            start, offset = page['next'], page['next_offset']
        else:
            pytest.fail('Continuation made no progress')
        assert invoked.call_count == 1
    assert ''.join(seen) == original
    assert page['items'][-1]['text'] == '最后一段'


def test_parse_cache_keys_include_content_and_expire_without_returning_old_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moment = [10.0]
    monkeypatch.setattr(reader_module, 'monotonic', lambda: moment[0])
    reader = WorkspaceFileReader()
    with patch.object(reader, '_invoke', wraps=reader._invoke) as invoked:
        assert reader.read_document(b'old', 'report.txt')['items'][0]['text'] == 'old'
        assert reader.read_document(b'new', 'report.txt')['items'][0]['text'] == 'new'
        reader.read_document(b'old', 'report.txt')
        assert invoked.call_count == 2
        moment[0] += 301
        reader.read_document(b'old', 'report.txt')
        assert invoked.call_count == 3


def test_invalid_offset_does_not_skip_text_and_explicit_block_limit_still_works() -> None:
    reader = WorkspaceFileReader()
    page = reader.read_document(b'one\ntwo\nthree', 'lines.txt', start=2, limit=1)
    assert page['items'] == [{'number': 2, 'text': 'two'}]
    assert page['next'] == 3 and page['next_offset'] == 0 and not page['complete']
    with pytest.raises(AppError, match='续读位置'):
        reader.read_document(b'one', 'lines.txt', offset=4)


def test_pdf_batches_keep_page_numbers_and_expose_pages_without_extractable_text() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(35):
        writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    reader = WorkspaceFileReader()
    first = reader.read_document(output.getvalue(), 'pages.pdf')
    assert first['next'] == 33 and first['next_offset'] == 0 and first['has_more']
    last = reader.read_document(output.getvalue(), 'pages.pdf', start=33)
    assert [item['number'] for item in last['items']] == [33, 34, 35]
    assert last['textless_pages'] == [33, 34, 35] and not last['has_more']


async def test_budgeted_tool_returns_structured_page_instead_of_a_generic_preview() -> None:
    tools = make_tools()
    tools.context.reader = WorkspaceFileReader()
    tools.progress.tool_result_tokens = 2200
    text = 'HEAD' + '原始资料汉字。' * 4000 + 'TAIL'
    info = SimpleNamespace(id=uuid4(), original_filename='report.docx')
    with patch.object(tools, '_file', AsyncMock(return_value=(info, word_blocks([text]), 'inputs/report.docx'))):
        shown = await tools.invoke('read_document', {'file_id': str(info.id)}, 'read-1')
    assert tool_result_tokens(shown) <= 2200
    assert shown['data']['items'][0]['text'].startswith('HEAD')
    assert shown['data']['next'] == 1 and shown['data']['next_offset'] > 0
    assert shown['data']['truncated'] and not shown['data']['complete'] and 'preview' not in shown['data']
    assert tools.progress.tool_result_tokens == 2200 - tool_result_tokens(shown)
    assert shown['source'] == ['file:' + str(info.id)]


async def test_warm_parse_cache_still_requires_file_authorization_on_every_read() -> None:
    tools = make_tools()
    tools.context.reader = WorkspaceFileReader()
    info = SimpleNamespace(id=uuid4(), original_filename='report.docx')
    authorized = (info, word_blocks(['Private report']), 'inputs/report.docx')
    with patch.object(
        tools, '_file', AsyncMock(side_effect=[authorized, AppError('forbidden', '权限已撤回', 403)])
    ) as read:
        assert (await tools.invoke('read_document', {'file_id': str(info.id)}, 'first'))['data']['complete']
        with pytest.raises(AppError, match='权限已撤回'):
            await tools.invoke('read_document', {'file_id': str(info.id)}, 'second')
    assert read.await_count == 2


def test_document_allowance_reserves_output_history_tools_and_unknown_capacity_fallback() -> None:
    binding = ModelBinding(
        model_id=uuid4(),
        connection_id=uuid4(),
        upstream_model='fixture',
        max_output_tokens=4096,
        context_window_tokens=16384,
    )
    short = (AgentModelMessage(role='user', content='阅读文档'),)
    long = (*short, AgentModelMessage(role='tool', content='历史内容' * 500, tool_call_id='read-old'))
    tools = ({'type': 'function', 'function': {'name': 'read_document', 'description': '说明' * 100}},)
    allowance = document_allowance(binding, None, short, ())
    assert 0 < allowance < 16384 - binding.output_reservation_tokens
    assert document_allowance(binding, None, long, tools) < allowance
    assert document_allowance(binding, 1000, short, tools) == 0
    unknown = binding.model_copy(update={'context_window_tokens': None})
    assert 0 < document_allowance(unknown, None, short, ()) < 65536


def test_tool_schema_estimation_uses_the_same_text_rules_as_history() -> None:
    schemas = ({'type': 'function', 'function': {'name': 'read_document', 'description': '读取文档'}},)
    assert estimate_tool_schemas(schemas) == estimate_messages(
        (ModelMessage('system', json.dumps(schemas, ensure_ascii=False)),)
    )
    assert estimate_tool_schemas(()) == 0
