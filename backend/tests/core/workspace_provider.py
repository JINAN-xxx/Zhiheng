"""Deterministic OpenAI wire fixture; actual platform runners and LangChain remain active."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def office_code(path: str, *, revision: bool) -> str:
    if revision:
        return """from docx import Document
from pathlib import Path
document = Document('outputs/report.docx')
document.add_paragraph('Revision two: checked against the preserved source.')
document.save('outputs/report.docx')
assert Path('outputs/amount.xlsx').exists()
assert Path('working/empty-marker').read_bytes() == b''
print('revision complete')
"""
    return f"""import csv
from pathlib import Path
from openpyxl import Workbook
from docx import Document
with open({path!r}, encoding='utf-8-sig', newline='') as source:
    rows = list(csv.DictReader(source))
total = sum(int(row['amount']) for row in rows)
assert total == 200
Path('outputs').mkdir(exist_ok=True)
workbook = Workbook()
sheet = workbook.active
sheet.title = 'Amounts'
sheet.append(['department', 'amount'])
for row in rows:
    sheet.append([row['department'], int(row['amount'])])
sheet.append(['Total', total])
workbook.save('outputs/amount.xlsx')
document = Document()
document.add_heading('Sales report', 0)
document.add_paragraph('Total amount: ' + str(total))
table = document.add_table(rows=1, cols=2)
table.rows[0].cells[0].text = 'Department'
table.rows[0].cells[1].text = 'Amount'
for row in rows:
    cells = table.add_row().cells
    cells[0].text, cells[1].text = row['department'], row['amount']
document.save('outputs/report.docx')
Path('working/empty-marker').touch()
print('files created' + chr(0) + 'total=' + str(total))
"""


class WorkspaceProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        super().__init__(('127.0.0.1', 0), WorkspaceProviderHandler)

    def response(self, body: dict[str, object]) -> dict[str, object]:
        messages = body['messages']
        if body['model'] == 'workspace-controls':
            return {'role': 'assistant', 'content': '你好\x00，已保留原始模型文本。'}
        if not body.get('tools'):
            return {
                'role': 'assistant',
                'content': '已完成核对，成果已保存。'
                if body['model'] in {'workspace-office', 'workspace-repair'}
                else '你好，可以直接告诉我需要什么帮助。',
            }
        if body['model'] == 'workspace-greeting':
            return {'role': 'assistant', 'content': '你好，可以直接告诉我需要什么帮助。'}
        user_index = max(index for index, item in enumerate(messages) if item['role'] == 'user')
        revision = 'REVISION' in str(messages[user_index]['content'])
        returned = [item for item in messages[user_index + 1 :] if item['role'] == 'tool']
        if body['model'] == 'workspace-repair':
            if len(returned) in {0, 2}:
                return self.tool(
                    'write_file',
                    {'path': 'outputs/report.txt', 'content': '' if not returned else 'repaired report'},
                    'empty' if not returned else 'repair',
                )
            if len(returned) in {1, 3}:
                return self.tool('publish_outputs', {}, 'publish-empty' if len(returned) == 1 else 'publish-fixed')
            return {'role': 'assistant', 'content': '空文件已补充正文并通过发布检查。'}
        if not returned and not revision:
            system = next(str(item['content']) for item in messages if item['role'] == 'system')
            metadata = json.JSONDecoder().raw_decode(system.split('任务目标与资料：\n', 1)[1])[0]
            return self.tool('read_sheet', {'file_id': metadata['inputs'][0]['file_id']}, 'read-source')
        code_index = 0 if revision else 1
        if len(returned) == code_index:
            path = '' if revision else json.loads(returned[0]['content'])['data']['workspace_path']
            return self.tool(
                'run_python', {'code': office_code(path, revision=revision)}, 'revise' if revision else 'create'
            )
        if len(returned) == code_index + 1:
            return self.tool('publish_outputs', {}, 'publish-revision' if revision else 'publish-first')
        return {'role': 'assistant', 'content': '文件已通过工具检查并登记。'}

    @staticmethod
    def tool(name: str, arguments: dict[str, object], identifier: str) -> dict[str, object]:
        return {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}
            ],
        }


class WorkspaceProviderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    'data': [
                        {'id': name}
                        for name in (
                            'workspace-greeting',
                            'workspace-office',
                            'workspace-controls',
                            'workspace-repair',
                        )
                    ]
                }
            ).encode()
        )

    def do_POST(self) -> None:
        server: WorkspaceProvider = self.server
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        server.requests.append(body)
        message = server.response(body)
        finish = 'tool_calls' if message.get('tool_calls') else 'stop'
        common = {'id': 'workspace-fixture', 'created': 1, 'model': body['model']}
        usage = {'prompt_tokens': 17, 'completion_tokens': 3, 'total_tokens': 20}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
        self.end_headers()
        try:
            if not body.get('stream'):
                response = {
                    **common,
                    'object': 'chat.completion',
                    'usage': usage,
                    'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                }
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode())
                return
            delta = {key: value for key, value in message.items() if key != 'role'}
            if 'tool_calls' in delta:
                delta['tool_calls'] = [{'index': index, **call} for index, call in enumerate(delta['tool_calls'])]
            chunks = [
                {
                    **common,
                    'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}],
                },
                {
                    **common,
                    'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                },
                {**common, 'object': 'chat.completion.chunk', 'choices': [], 'usage': usage},
            ]
            for chunk in chunks:
                self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *args: object) -> None:
        pass
