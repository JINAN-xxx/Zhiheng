"""A local HTTP provider: tests exercise LangChain rather than mock its methods."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ProviderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.request_headers: list[dict[str, str]] = []
        self.release = threading.Event()
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
        if body['model'] == 'silent':
            server.release.wait(timeout=5)
        try:
            for content, finish, usage in [
                ('你', None, None),
                ('好', 'stop', None),
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
