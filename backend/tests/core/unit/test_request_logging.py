import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import uvicorn
from starlette.types import Receive, Scope, Send

FAKE_SHARE_TOKEN = 'qa_only_' + 'A' * 35


async def logging_probe_app(scope: Scope, receive: Receive, send: Send) -> None:
    assert scope['type'] == 'http'
    if scope['path'].endswith('/raise'):
        headers = dict(scope['headers'])
        referer = headers.get(b'referer', b'').decode('ascii')
        target = scope['path'] + '?' + scope['query_string'].decode('ascii')
        raise RuntimeError(f'QA exception at {target}; Referer={referer}')
    await send({'type': 'http.response.start', 'status': 204, 'headers': []})
    await send({'type': 'http.response.body', 'body': b''})


@asynccontextmanager
async def logging_server(protocol: Literal['h11', 'httptools']) -> AsyncIterator[str]:
    if os.getenv('ZHIHENG_REQUEST_LOG_BASELINE') == '1':
        from uvicorn.config import LOGGING_CONFIG

        configuration = LOGGING_CONFIG
    else:
        from zhiheng.api.request_logging import uvicorn_log_config

        configuration = uvicorn_log_config()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(
        uvicorn.Config(
            logging_probe_app,
            http=protocol,
            root_path='/qa-root',
            lifespan='off',
            ws='none',
            log_config=configuration,
            proxy_headers=False,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
        assert server.started
        yield f'http://127.0.0.1:{listener.getsockname()[1]}'
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        listener.close()


@pytest.mark.parametrize('protocol', ['h11', 'httptools'])
async def test_actual_uvicorn_logs_redact_share_targets_and_exceptions(
    protocol: Literal['h11', 'httptools'], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async with logging_server(protocol) as base_url:
        async with httpx.AsyncClient(base_url=base_url) as client:
            for prefix in ('/api/shared/messages/', '/share/'):
                response = await client.get(prefix + FAKE_SHARE_TOKEN, params={'copy': FAKE_SHARE_TOKEN})
                assert response.status_code == 204
            ordinary = await client.put('/api/chat/conversations?limit=2')
            assert ordinary.status_code == 204
            failed = await client.get(
                '/api/shared/messages/' + FAKE_SHARE_TOKEN + '/raise', params={'copy': FAKE_SHARE_TOKEN}
            )
            assert failed.status_code == 500
            for prefix in ('/share/', '/%73hare/'):
                referer_failure = await client.get(
                    '/api/qa/raise', headers={'Referer': base_url + prefix + FAKE_SHARE_TOKEN}
                )
                assert referer_failure.status_code == 500
        captured = capsys.readouterr()
        output = captured.out + captured.err
        (tmp_path / 'server.log').write_text(output)
        assert FAKE_SHARE_TOKEN not in output, output
        assert 'PUT /qa-root/api/chat/conversations?limit=2 HTTP/1.1' in output
        assert 'GET /qa-root/api/shared/messages/[REDACTED] HTTP/1.1' in output
        assert 'GET /qa-root/share/[REDACTED] HTTP/1.1' in output
        assert '204' in output and '500' in output and 'RuntimeError' in output


@pytest.mark.parametrize(
    ('target', 'expected'),
    [
        ('/api/chat/conversations?limit=2', '/api/chat/conversations?limit=2'),
        ('/api/chat/messages/example/shares', '/api/chat/messages/example/shares'),
        ('/api/shared/messages/qa-token?copy=qa-token', '/api/shared/messages/[REDACTED]'),
        ('/share/qa-token?copy=qa-token', '/share/[REDACTED]'),
        ('/root/prefix/api/shared/messages/qa-token', '/root/prefix/api/shared/messages/[REDACTED]'),
        ('/root/prefix/share/qa-token', '/root/prefix/share/[REDACTED]'),
        ('/root/prefix/%73hare/qa-token', '/root/prefix/%73hare/[REDACTED]'),
    ],
)
def test_access_redaction_preserves_other_requests(target: str, expected: str) -> None:
    from zhiheng.api.request_logging import ShareTokenLogFilter

    record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1, '%s %s %s %s %s', (), None)
    record.args = ('127.0.0.1:12345', 'GET', target, '1.1', 204)
    assert ShareTokenLogFilter().filter(record)
    assert record.args == ('127.0.0.1:12345', 'GET', expected, '1.1', 204)


def test_exception_message_redacts_encoded_share_referer() -> None:
    from zhiheng.api.request_logging import ShareTokenLogFilter

    record = logging.LogRecord(
        'uvicorn.error', logging.ERROR, __file__, 1, 'QA Referer=%s', ('http://qa/%73hare/' + FAKE_SHARE_TOKEN,), None
    )
    assert ShareTokenLogFilter().filter(record)
    assert FAKE_SHARE_TOKEN not in record.getMessage()


def test_serve_cli_passes_redacting_log_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import zhiheng.cli.main as cli

    observed: list[dict[str, object]] = []

    def serve(application: object, *, host: str, port: int, proxy_headers: bool, log_config: dict[str, object]) -> None:
        observed.append(log_config)

    monkeypatch.setattr(cli, 'Settings', object)
    monkeypatch.setattr(cli, 'create_app', lambda settings: logging_probe_app)
    monkeypatch.setattr(cli.uvicorn, 'run', serve)
    assert cli.main(['serve']) == 0
    assert observed[0]['disable_existing_loggers'] is False
    assert 'share_tokens' in observed[0]['filters']
