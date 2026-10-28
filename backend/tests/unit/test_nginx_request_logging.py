import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

FAKE_SHARE_TOKEN = 'qa_only_' + 'B' * 35
NGINX_CONFIG = Path(__file__).resolve().parents[3] / 'frontend' / 'nginx.core.conf'


def docker(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=15, check=False)


@pytest.fixture
def nginx_probe(tmp_path: Path) -> Iterator[str]:
    image = os.getenv('ZHIHENG_TEST_NGINX_IMAGE')
    if not image:
        pytest.skip('An existing frontend image must be explicitly selected for the isolated Nginx probe')
    config = tmp_path / 'nginx.conf'
    config.write_text(
        NGINX_CONFIG.read_text().replace('set $core_api http://api:19090;', 'set $core_api http://127.0.0.1:9;')
    )
    name = 'qa-share-logging-' + uuid4().hex
    try:
        started = docker(
            'run',
            '--detach',
            '--name',
            name,
            '--network',
            'none',
            '--read-only',
            '--tmpfs',
            '/var/cache/nginx',
            '--tmpfs',
            '/var/run',
            '--mount',
            f'type=bind,src={config},dst=/etc/nginx/conf.d/default.conf,readonly',
            '--entrypoint',
            'nginx',
            image,
            '-g',
            'daemon off;',
        )
        assert started.returncode == 0, started.stderr
        syntax = docker('exec', name, 'nginx', '-t')
        (tmp_path / 'nginx-syntax.log').write_text(syntax.stdout + syntax.stderr)
        assert syntax.returncode == 0, syntax.stderr + docker('logs', name).stderr
        for _ in range(30):
            ready = docker('exec', name, 'wget', '-q', '-O', '/dev/null', 'http://127.0.0.1/platform.html')
            if ready.returncode == 0:
                break
            time.sleep(0.02)
        assert ready.returncode == 0, ready.stderr
        yield name
    finally:
        removed = docker('rm', '--force', name)
        assert removed.returncode == 0, removed.stderr
        assert docker('inspect', name).returncode != 0


def request(name: str, target: str, *, referer: str = '') -> str:
    arguments = ['exec', name, 'wget', '-S', '-O', '/dev/null', '-T', '3']
    if referer:
        arguments.extend(['--header', 'Referer: ' + referer])
    result = docker(*arguments, 'http://127.0.0.1' + target)
    return result.stdout + result.stderr


def test_nginx_share_headers_and_all_log_channels_hide_fake_tokens(nginx_probe: str, tmp_path: Path) -> None:
    response = request(nginx_probe, '/share/' + FAKE_SHARE_TOKEN + '?copy=' + FAKE_SHARE_TOKEN)
    request(nginx_probe, '/%73hare/' + FAKE_SHARE_TOKEN + '?copy=' + FAKE_SHARE_TOKEN)
    request(nginx_probe, '/qa-root/share/' + FAKE_SHARE_TOKEN)
    request(nginx_probe, '/api/shared/messages/' + FAKE_SHARE_TOKEN + '?copy=' + FAKE_SHARE_TOKEN)
    private_referer = 'http://qa/share/' + FAKE_SHARE_TOKEN + '?copy=' + FAKE_SHARE_TOKEN
    request(nginx_probe, '/api/models?ordinary=1', referer=private_referer)
    request(nginx_probe, '/platform.html', referer=private_referer)
    request(nginx_probe, '/api/models?ordinary=2', referer='http://qa/ordinary')
    captured = docker('logs', nginx_probe)
    output = captured.stdout + captured.stderr
    (tmp_path / 'nginx.log').write_text(output)
    (tmp_path / 'share-response-headers.log').write_text(response)
    assert FAKE_SHARE_TOKEN not in output, output
    assert 'GET /api/models?ordinary=1 HTTP/1.1' in output
    assert 'GET /api/models?ordinary=2 HTTP/1.1' in output
    assert 'http://qa/ordinary' in output
    assert 'connect() failed' in output and '502' in output
    assert 'Cache-Control: no-store' in response
    assert 'X-Robots-Tag: noindex' in response
    assert 'Referrer-Policy: no-referrer' in response
