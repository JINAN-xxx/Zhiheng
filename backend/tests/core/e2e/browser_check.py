"""Built frontend -> installed API -> ARQ -> local HTTP provider -> PostgreSQL."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_ROOT))

# Support direct execution of this test tool without installing the test package.
from tests.core.provider import ProviderServer  # noqa: E402


async def create_database(url: str, name: str, *, drop: bool = False) -> None:
    engine = create_async_engine(url, isolation_level='AUTOCOMMIT')
    try:
        async with engine.connect() as connection:
            statement = f'DROP DATABASE "{name}" WITH (FORCE)' if drop else f'CREATE DATABASE "{name}"'
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@contextmanager
def provider_service() -> Iterator[ProviderServer]:
    provider = ProviderServer()
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        yield provider
    finally:
        provider.release.set()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)


def add_model(page: Page, name: str, upstream: str) -> None:
    page.get_by_role('button', name='接入模型').click()
    dialog = page.locator('.ant-drawer-open .ant-drawer-content')
    dialog.get_by_placeholder('例如：日常助手').fill(name)
    dialog.get_by_placeholder('填写服务商提供的模型 ID').fill(upstream)
    dialog.locator('.ant-input-number-input').fill('64')
    with page.expect_response(
        lambda result: result.url.endswith('/api/models') and result.request.method == 'POST'
    ) as request:
        dialog.get_by_role('button', name='保存模型', exact=True).click()
    assert request.value.status == 201, request.value.text()
    expect(dialog).not_to_be_visible()


def configure_models(
    page: Page, provider: ProviderServer, artifacts: Path, provider_base_url: str | None = None
) -> None:
    page.get_by_role('link', name='模型目录', exact=True).click()
    page.get_by_role('tab', name='凭据管理').click()
    page.get_by_role('button', name='新增凭据').click()
    dialog = page.locator('.ant-drawer-open .ant-drawer-content')
    dialog.get_by_placeholder('例如：团队模型服务').fill('本地协议服务')
    dialog.get_by_placeholder('https://api.example.com/v1').fill(
        provider_base_url or f'http://127.0.0.1:{provider.server_port}/v1'
    )
    dialog.get_by_placeholder('密钥仅用于本次提交，保存后不会回显').fill('local-test-key')
    with page.expect_response(
        lambda result: result.url.endswith('/api/models/connections') and result.request.method == 'POST'
    ) as request:
        dialog.get_by_role('button', name='保存连接', exact=True).click()
    assert request.value.status == 201, request.value.text()
    assert 'local-test-key' not in request.value.text()
    expect(dialog).not_to_be_visible()
    add_model(page, '日常助手', 'local-text')
    page.get_by_role('button', name='测试连接', exact=True).click()
    expect(page.locator('.test-result').get_by_text('已完成', exact=True)).to_be_visible(timeout=15000)
    page.screenshot(path=str(artifacts / 'models.png'), full_page=True)
    add_model(page, '慢速助手', 'silent')


def exercise_chat(page: Page, provider: ProviderServer, artifacts: Path) -> None:
    page.get_by_role('link', name='对话体验', exact=True).click()
    composer = page.get_by_role('textbox', name='消息内容')
    for question, expected_replies in [('第一轮真实聊天', 1), ('第二轮沿用同一连接', 2)]:
        composer.fill(question)
        page.get_by_role('button', name='发送消息', exact=True).click()
        expect(page.locator('.run-state')).to_have_text('已完成', timeout=15000)
        expect(page.locator('.message-content').filter(has_text='你好')).to_have_count(expected_replies)
    page.screenshot(path=str(artifacts / 'chat-desktop.png'), full_page=True)
    page.reload()
    page.get_by_role('button', name='历史记录', exact=True).click()
    history = page.get_by_role('dialog', name='历史记录', exact=True)
    expect(history).to_be_visible()
    history.locator('.conversation-item').first.click()
    expect(history).not_to_be_visible()
    expect(page.locator('.message-content').filter(has_text='你好')).to_have_count(2)
    page.locator('.model-select .ant-select-selector').click()
    page.locator('.ant-select-item-option').filter(has_text='慢速助手').click()
    before_silent = len(provider.requests)
    composer.fill('停止这次静默生成')
    page.get_by_role('button', name='发送消息', exact=True).click()
    deadline = time.monotonic() + 5
    while len(provider.requests) == before_silent:
        assert time.monotonic() < deadline, 'Worker did not reach the local provider'
        page.wait_for_timeout(50)
    page.get_by_role('button', name='停止生成').click()
    expect(page.locator('.run-state')).to_have_text('已停止', timeout=5000)


def usage_and_logout(page: Page, base_url: str, artifacts: Path) -> None:
    page.get_by_role('link', name='用量与调用', exact=True).click()
    page.get_by_role('tab', name='历史 Token', exact=True).click()
    expect(page.locator('.usage-table tbody tr.ant-table-row')).to_have_count(4)
    expect(page.locator('.usage-table').get_by_text('待核对')).not_to_have_count(0)
    page.screenshot(path=str(artifacts / 'usage.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='打开主菜单').click()
    page.locator('.ant-drawer-open .ant-drawer-content').get_by_role('link', name='对话体验', exact=True).click()
    expect(page.get_by_role('textbox', name='消息内容')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    page.screenshot(path=str(artifacts / 'chat-mobile.png'), full_page=True)
    page.get_by_role('button', name='打开主菜单', exact=True).click()
    page.locator('.platform-mobile-drawer').get_by_role('button', name='用户菜单', exact=True).click()
    page.get_by_role('menuitem', name='退出登录', exact=True).click()
    expect(page).to_have_url(f'{base_url}/login')
    assert page.context.request.get(f'{base_url}/api/auth/me').status == 401


def browser_flow(base_url: str, provider: ProviderServer, artifacts: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 960})
            page = context.new_page()
            errors: list[str] = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'{base_url}/login')
            page.get_by_placeholder('账号', exact=True).fill('admin')
            page.get_by_placeholder('密码', exact=True).fill('browser-test-password')
            with page.expect_response(
                lambda result: result.url.endswith('/api/auth/login') and result.request.method == 'POST'
            ) as login_request:
                page.get_by_role('button', name='登录', exact=True).click()
            assert login_request.value.status == 200, login_request.value.text()
            expect(page).to_have_url(f'{base_url}/chat')
            configure_models(page, provider, artifacts)
            exercise_chat(page, provider, artifacts)
            usage_and_logout(page, base_url, artifacts)
            assert not errors, errors
        except Exception:
            with suppress(Exception):
                page.screenshot(path=str(artifacts / 'failure.png'), full_page=True)
                (artifacts / 'failure.html').write_text(page.content())
            raise
        finally:
            browser.close()


def child_environment(database_url: str, redis_url: str, base_url: str, *, files_root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(('ZHIHENG_', 'PYTHONPATH'))}
    env.update(
        {
            'ZHIHENG_FILES_ROOT': str(files_root),
            'ZHIHENG_DATABASE_URL': database_url,
            'ZHIHENG_REDIS_URL': redis_url,
            'ZHIHENG_CREDENTIAL_KEY': Fernet.generate_key().decode(),
            'ZHIHENG_COOKIE_SECURE': 'false',
            'ZHIHENG_ALLOWED_ORIGINS': json.dumps([base_url]),
            'ZHIHENG_WORKER_QUEUE': f'core:e2e:{uuid4().hex}',
            'ZHIHENG_PROVIDER_TIMEOUT_SECONDS': '15',
        }
    )
    if 'ZHIHENG_E2E_PYTHON' not in os.environ:
        env['PYTHONPATH'] = str(BACKEND_ROOT / 'src')
    return env


@contextmanager
def application_processes(
    python: str,
    env: dict[str, str],
    api_port: int,
    artifacts: Path,
) -> Iterator[list[subprocess.Popen[bytes]]]:
    processes: list[subprocess.Popen[bytes]] = []
    with ExitStack() as stack:
        try:
            for name, command in [
                ('api', ['serve', '--host', '127.0.0.1', '--port', str(api_port)]),
                ('worker', ['worker']),
            ]:
                output = stack.enter_context((artifacts / f'{name}.log').open('w'))
                processes.append(
                    subprocess.Popen(
                        [python, '-m', 'zhiheng', *command],
                        env=env,
                        cwd='/tmp',
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
                )
            yield processes
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                wait_for_process_exit(process)


def wait_for_process_exit(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_ready(api_port: int, processes: list[subprocess.Popen[bytes]], artifacts: Path) -> None:
    deadline = time.monotonic() + 25
    with httpx.Client(trust_env=False, timeout=1) as client:
        while True:
            with suppress(httpx.HTTPError):
                if client.get(f'http://127.0.0.1:{api_port}/health/ready').status_code == 200:
                    return
            if any(process.poll() is not None for process in processes) or time.monotonic() > deadline:
                raise AssertionError(f'Core API/worker failed to become ready; see {artifacts}')
            time.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:15191')
    parser.add_argument('--api-port', type=int, default=19090)
    parser.add_argument('--artifacts-dir', type=Path, default=Path('/tmp/zhiheng-core-browser-artifacts'))
    args = parser.parse_args()
    database_url = os.environ['ZHIHENG_TEST_DATABASE_URL']
    database_name = f'core_browser_{uuid4().hex}'
    python = os.getenv('ZHIHENG_E2E_PYTHON', sys.executable)
    instance_url = make_url(database_url).set(database=database_name).render_as_string(hide_password=False)
    with TemporaryDirectory(prefix='zhiheng-core-e2e-files-') as files_root:
        env = child_environment(
            instance_url, os.environ['ZHIHENG_TEST_REDIS_URL'], args.base_url, files_root=Path(files_root)
        )
        artifacts = args.artifacts_dir
        artifacts.mkdir(parents=True, exist_ok=True)
        asyncio.run(create_database(database_url, database_name))
        try:
            subprocess.run([python, '-m', 'zhiheng', 'migrate'], env=env, cwd='/tmp', check=True)
            subprocess.run(
                [python, '-m', 'zhiheng', 'create-admin', '--username', 'admin', '--password-stdin'],
                input='browser-test-password\n',
                text=True,
                env=env,
                cwd='/tmp',
                check=True,
            )
            with (
                provider_service() as provider,
                application_processes(python, env, args.api_port, artifacts) as processes,
            ):
                wait_ready(args.api_port, processes, artifacts)
                browser_flow(args.base_url, provider, artifacts)
            print(
                f'Core browser flow passed: login, model setup/test, two chat turns, history, cancellation, usage, mobile, logout. Artifacts: {artifacts}'
            )
            return 0
        finally:
            asyncio.run(create_database(database_url, database_name, drop=True))


if __name__ == '__main__':
    raise SystemExit(main())
