"""A local HTTP provider: tests exercise LangChain rather than mock its methods."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ProviderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.request_headers: list[dict[str, str]] = []
        self.release = threading.Event()
        self.embedding_zero = False
        self.graph_block = False
        self.graph_parallel = False
        super().__init__(('127.0.0.1', 0), ProviderHandler)


class ProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: ProviderServer = self.server
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        server.requests.append(body)
        server.request_headers.append({name.lower(): value for name, value in self.headers.items()})
        if self.path.endswith(('/embeddings', '/rerank')):
            self.structured(body)
            return
        if body['model'] == 'rejected':
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"private-provider-details","type":"authentication_error"}}')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        if body['model'] == 'truncated':
            self.wfile.write(
                b'data: {"id":"partial","object":"chat.completion.chunk","created":1,"model":"truncated","choices":[{"index":0,"delta":{"content":"half answer"},"finish_reason":null}]}\n\n'
            )
            self.wfile.flush()
            return
        if str(body['model']).startswith('reasoning'):
            reasoning_chunk = {
                'id': 'reasoning',
                'object': 'chat.completion.chunk',
                'created': 1,
                'model': body['model'],
                'choices': [{'index': 0, 'delta': {'reasoning_content': '先思考'}, 'finish_reason': None}],
            }
            self.wfile.write(('data: ' + json.dumps(reasoning_chunk, ensure_ascii=False) + '\n\n').encode())
            self.wfile.flush()
        if body['model'] in ('silent', 'reasoning-silent') or (body['model'] == 'graph' and server.graph_block):
            server.release.wait(timeout=5)
        try:
            output = '你好'
            if body['model'] == 'graph':
                prompt = '\n'.join(message['content'] for message in body['messages'])
                if body.get('response_format') == {'type': 'json_object'}:
                    output = json.dumps(
                        {'high_level_keywords': ['合作'], 'low_level_keywords': ['甲机构']}, ensure_ascii=False
                    )
                elif 'synthesize a list of descriptions' in prompt:
                    output = '双方持续合作研究。'
                elif server.graph_parallel and len(body['messages']) > 2:
                    output = '<|COMPLETE|>'
                elif server.graph_parallel and 'Knowledge Graph Specialist' in prompt:
                    samples = re.findall(r'审查样本(\d+)', prompt)
                    sample = samples[-1] if samples else str(len(server.requests))
                    output = (
                        f'entity<|#|>甲机构<|#|>Organization<|#|>甲机构参与审查样本{sample}研究。\n'
                        f'entity<|#|>乙公司<|#|>Organization<|#|>乙公司参与审查样本{sample}研究。\n'
                        f'relation<|#|>甲机构<|#|>乙公司<|#|>合作<|#|>双方审查样本{sample}合作。\n<|COMPLETE|>'
                    )
                elif 'Knowledge Graph Specialist' in prompt:
                    output = 'entity<|#|>甲机构<|#|>Organization<|#|>甲机构参与研究。\nentity<|#|>乙公司<|#|>Organization<|#|>乙公司参与研究。\nrelation<|#|>甲机构<|#|>乙公司<|#|>合作<|#|>双方合作。\n<|COMPLETE|>'
                elif 'summar' in prompt.lower():
                    output = '双方持续合作研究。'
                else:
                    output = '<|COMPLETE|>'
            pieces = (
                [(output, 'stop', None)] if body['model'] == 'graph' else [('你', None, None), ('好', 'stop', None)]
            )
            for content, finish, usage in [
                *pieces,
                ('', None, {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}),
            ]:
                if usage and body['model'] == 'no-usage':
                    continue
                chunk = {
                    'id': 'local-provider',
                    'object': 'chat.completion.chunk',
                    'created': 1,
                    'model': body['model'],
                    'choices': [] if usage else [{'index': 0, 'delta': {'content': content}, 'finish_reason': finish}],
                }
                if usage:
                    wire_usage = usage
                    if body['model'] == 'partial-usage':
                        wire_usage = {'prompt_tokens': 8, 'total_tokens': 10}
                    elif body['model'] == 'missing-input':
                        wire_usage = {'completion_tokens': 2}
                    if body['model'] == 'cached':
                        wire_usage = {
                            **wire_usage,
                            'prompt_tokens_details': {'cached_tokens': 4},
                            'completion_tokens_details': {'reasoning_tokens': 1},
                        }
                    chunk['usage'] = wire_usage
                self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode())
                self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def structured(self, body: dict[str, object]) -> None:
        if self.path.endswith('/embeddings'):
            response = {
                'data': [
                    {'index': index, 'embedding': [0.1, 0.2] if index == 0 else [0.3, 0.4]}
                    for index, _ in enumerate(body['input'])
                ],
                'usage': {'prompt_tokens': 6, 'total_tokens': 6},
            }
            if self.server.embedding_zero:
                for row in response['data']:
                    row['embedding'] = [0.0, 0.0]
        else:
            response = {
                'results': [{'index': 0, 'relevance_score': 0.9}],
                'meta': {'billed_units': {'search_units': 2}},
                'usage': {'prompt_tokens': 6, 'completion_tokens': 0},
            }
        if str(body['model']).endswith('-no-usage'):
            response.pop('usage', None)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, _format: str, *args: object) -> None:
        pass
