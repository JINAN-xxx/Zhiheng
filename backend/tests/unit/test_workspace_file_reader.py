import io
import json
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from zhiheng.modules.files.adapters.workspace import MAX_RESULT_BYTES, WorkspaceFileReader
from zhiheng.shared.errors import AppError


def test_csv_pages_keep_real_row_numbers_and_chinese_encoding() -> None:
    reader = WorkspaceFileReader()
    content = '部门,金额\n研发,120\n销售,80\n'.encode('gb18030')
    inspected = reader.inspect(content, '金额.csv')
    assert inspected['columns'] == 2
    page = reader.read_sheet(content, '金额.csv', start_row=2, limit=1)
    assert page['rows'] == [['研发', '120']]
    assert page['start_row'] == 2 and page['next_row'] == 3


def test_excel_structure_and_formula_reading_are_read_only() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.title = '金额'
    workbook.active.append(['金额', '公式'])
    workbook.active.append([120, '=A2*2'])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    content = output.getvalue()
    reader = WorkspaceFileReader()
    assert reader.inspect(content, '金额.xlsx')['sheets'] == [{'name': '金额', 'rows': 2, 'columns': 2}]
    assert reader.read_sheet(content, '金额.xlsx', sheet='金额', start_row=2)['rows'] == [[120, '=A2*2']]
    with pytest.raises(AppError) as error:
        reader.read_sheet(content, '金额.xlsx', sheet='不存在')
    assert error.value.code == 'sheet_not_found'


def word(content: str) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr('word/document.xml', content)
    return result.getvalue()


def test_docx_paragraphs_and_tables_preserve_document_order() -> None:
    content = word("""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:r><w:t>标题</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>研发</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>120</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    <w:p><w:r><w:t>结尾</w:t></w:r></w:p></w:body></w:document>""")
    page = WorkspaceFileReader().read_document(content, 'report.docx', start=2, limit=1)
    assert page['unit'] == 'block' and page['total'] == 3
    assert page['items'] == [{'number': 2, 'text': '研发 | 120'}]
    assert page['next'] == 3


@pytest.mark.parametrize(
    'xml',
    [
        '<!DOCTYPE doc [<!ENTITY x "expanded">]><doc>&x;</doc>',
        '<!DOCTYPE doc SYSTEM "file:///etc/passwd"><doc/>',
    ],
)
def test_office_xml_entities_are_rejected(xml: str) -> None:
    with pytest.raises(AppError) as error:
        WorkspaceFileReader().inspect(word(xml), 'report.docx')
    assert error.value.code == 'file_reader_failed'


def test_large_cell_previews_and_result_size_stay_bounded() -> None:
    content = (','.join(['\x01' * 500] * 50) + '\n') * 10
    result = WorkspaceFileReader().read_sheet(content.encode(), 'rows.csv', limit=10)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= MAX_RESULT_BYTES
    assert result['next_row'] is not None


def test_reader_timeout_is_reported_without_executing_model_code() -> None:
    with (
        patch(
            'zhiheng.modules.files.adapters.workspace.subprocess.run',
            side_effect=subprocess.TimeoutExpired('reader', 20),
        ),
        pytest.raises(AppError) as error,
    ):
        WorkspaceFileReader().inspect(b'hello', 'file.txt')
    assert error.value.code == 'parse_timeout'


def test_fixed_reader_child_does_not_receive_platform_credentials() -> None:
    native = subprocess.run
    environments = []

    def inspect_environment(*args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        environments.append(kwargs['env'])
        return native(*args, **kwargs)

    with patch('zhiheng.modules.files.adapters.workspace.subprocess.run', side_effect=inspect_environment):
        assert WorkspaceFileReader().read_document(b'first\nsecond\n', 'lines.txt', start=2)['items'] == [
            {'number': 2, 'text': 'second'}
        ]
    assert all(
        key
        in {
            'PATH',
            'LANG',
            'LC_ALL',
            'PYTHONPATH',
            'PYTHONDONTWRITEBYTECODE',
            'OPENBLAS_NUM_THREADS',
            'OMP_NUM_THREADS',
        }
        for key in environments[0]
    )
