"""Trusted read-only parser process; accepts bytes and page parameters, never generated code."""

import json
import sys

from zhiheng.modules.files.adapters.workspace import MAX_RESULT_BYTES, RESULT_METADATA_BYTES, _OfficeReader
from zhiheng.modules.files.parsing_contracts import MAX_INPUT_BYTES
from zhiheng.shared.errors import AppError

MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
CPU_LIMIT_SECONDS = 15
MAX_REQUEST_HEADER_BYTES = 8192


def main() -> None:
    if sys.platform != 'win32':
        import resource  # noqa: PLC0415 - OS-specific limits apply only inside this fixed child

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        header = sys.stdin.buffer.readline(MAX_REQUEST_HEADER_BYTES + 1)
        content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(header) > MAX_REQUEST_HEADER_BYTES or not content or len(content) > MAX_INPUT_BYTES:
            raise AppError('invalid_parse_input', '文件读取输入无效', 422)
        request = json.loads(header)
        operation, filename = request.get('operation'), request.get('filename')
        if not isinstance(filename, str):
            raise ValueError('Invalid filename')
        reader = _OfficeReader()
        if operation == 'inspect':
            data = reader.inspect(content, filename)
        elif operation == 'read_document':
            data = reader.read_document(content, filename, request.get('start', 1), request.get('limit', 40))
        elif operation == 'read_sheet':
            data = reader.read_sheet(
                content, filename, request.get('sheet', ''), request.get('start_row', 1), request.get('limit', 40)
            )
        else:
            raise ValueError('Invalid reader operation')
        reply = {'data': data}
    except AppError as error:
        reply = {'error': {'code': error.code, 'message': error.message}}
    except Exception:
        reply = {'error': {'code': 'file_reader_failed', 'message': '文件内容无法读取，请检查格式和编码'}}
    payload = json.dumps(reply, ensure_ascii=False, allow_nan=False)
    if len(payload.encode()) > MAX_RESULT_BYTES + RESULT_METADATA_BYTES:
        payload = json.dumps({'error': {'code': 'document_too_large', 'message': '文件读取结果超过限制'}})
    print(payload)


if __name__ == '__main__':
    main()
