"""Run every locally defined browser regression with per-test network evidence."""
from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
import time
import unittest
import weakref
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TEST_ROOT = Path(__file__).resolve().parent


def discover() -> list[dict[str, object]]:
    cases = []
    direct_source = re.compile(r"""(?:import|fetch)\s*[(]\s*['"]/src/""")
    for file in sorted(TEST_ROOT.glob('*regressions.py')):
        source = file.read_text()
        for cls in (node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)):
            methods = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            helper_needs_source = any(
                direct_source.search(ast.get_source_segment(source, node) or '')
                for node in methods if not node.name.startswith('test_')
            )
            for node in methods:
                if node.name.startswith('test_'):
                    cases.append({
                        'id': f'{file.stem}.{cls.name}.{node.name}',
                        'source': bool(helper_needs_source or direct_source.search(ast.get_source_segment(source, node) or '')),
                    })
    return cases


def safe_url(value: str) -> str:
    parsed = urlsplit(value)
    path = re.sub(r'^(/(?:api/shared/messages|share)/).*', r'\1[redacted]', parsed.path)
    query = parsed.query if path.startswith(('/src/', '/assets/', '/node_modules/')) else ''
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, ''))


def run_worker(args: argparse.Namespace) -> int:
    from playwright.async_api import Browser, BrowserContext, BrowserType, expect

    expect.set_options(timeout=15000)
    sys.path.insert(0, str(TEST_ROOT))
    cases = json.loads(Path(args.batch).read_text())
    result_file = args.output / f'worker-{args.worker_index}-results.jsonl'
    result_file.write_text('')
    active = {'id': ''}
    observations: dict[str, dict[str, object]] = {}
    seen = weakref.WeakSet()

    def instrument(page):
        if page in seen:
            return page
        seen.add(page)
        case_id = active['id']
        state = observations.setdefault(case_id, {
            'requests_failed': [], 'script_http_errors': [], 'console_errors': [],
            'page_errors': [], 'pending': {},
        })
        pending = state['pending']

        def requested(request):
            pending[id(request)] = {
                'url': safe_url(request.url), 'type': request.resource_type, 'started': time.time(),
            }

        def finished(request):
            pending.pop(id(request), None)

        def failed(request):
            pending.pop(id(request), None)
            state['requests_failed'].append({
                'url': safe_url(request.url), 'type': request.resource_type,
                'error': request.failure, 'time': time.time(),
            })

        async def response(received):
            if received.status >= 400 and received.request.resource_type in {'document', 'script', 'stylesheet'}:
                try:
                    body = (await received.text())[:800]
                except Exception:
                    body = ''
                state['script_http_errors'].append({
                    'url': safe_url(received.url), 'status': received.status, 'body': body,
                })

        page.on('request', requested)
        page.on('requestfinished', finished)
        page.on('requestfailed', failed)
        page.on('response', response)
        page.on('pageerror', lambda error: state['page_errors'].append(str(error)[:2000]))
        page.on('console', lambda message: state['console_errors'].append(message.text[:2000])
                if message.type in {'warning', 'error'} else None)
        return page

    original_context_page = BrowserContext.new_page
    original_browser_page = Browser.new_page

    async def context_page(self, *positional, **keywords):
        return instrument(await original_context_page(self, *positional, **keywords))

    async def browser_page(self, *positional, **keywords):
        return instrument(await original_browser_page(self, *positional, **keywords))

    BrowserContext.new_page = context_page
    Browser.new_page = browser_page

    if args.cdp_url:
        async def connect_browser(self, **_options):
            return await self.connect_over_cdp(args.cdp_url)
        BrowserType.launch = connect_browser
    elif args.executable_path or args.map_host:
        original_launch = BrowserType.launch

        async def launch_native(self, **options):
            if args.executable_path:
                options['executable_path'] = args.executable_path
            if args.map_host:
                options['args'] = [*options.get('args', []),
                    '--host-resolver-rules=MAP ' + args.map_host + ' 127.0.0.1',
                    '--no-proxy-server']
            return await original_launch(self, **options)

        BrowserType.launch = launch_native

    original_test_method = unittest.IsolatedAsyncioTestCase._callTestMethod

    def bounded_test_method(self, method):
        if inspect.iscoroutinefunction(method):
            async def bounded():
                timeout = args.case_timeout
                if timeout is None:
                    timeout = getattr(self, 'case_timeouts', {}).get(method.__name__, 90)
                return await asyncio.wait_for(method(), timeout)
            return original_test_method(self, bounded)
        return original_test_method(self, method)

    unittest.IsolatedAsyncioTestCase._callTestMethod = bounded_test_method

    class Results(unittest.TextTestResult):
        def startTest(self, test):
            if args.host_memory_status:
                deadline = time.monotonic() + 900
                last_notice = 0.0
                while True:
                    try:
                        pressure = json.loads(args.host_memory_status.read_text())
                    except (OSError, ValueError):
                        pressure = {}
                    if 'commit_limit' in pressure and 'commit_bytes' in pressure:
                        commit_ready = pressure['commit_limit'] - pressure['commit_bytes'] > 1536 * 1024 * 1024
                    else:
                        commit_ready = pressure.get('commit_percent', 100) < 92
                    if commit_ready and pressure.get('available_phys_mb', 0) > 1500:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Host memory pressure prevented starting the next browser case')
                    if time.monotonic() - last_notice > 15:
                        print('Waiting for host memory headroom before', test.id(), flush=True)
                        last_notice = time.monotonic()
                    time.sleep(2)
            active['id'] = test.id()
            case = next(row for row in cases if row['id'] == test.id())
            os.environ['PLATFORM_BROWSER_URL'] = args.source_url if case['source'] else args.base_url
            super().startTest(test)

        def stopTest(self, test):
            observations.pop(test.id(), None)
            super().stopTest(test)

        def record(self, test, outcome, detail='', subtest=None):
            state = observations.get(test.id(), {})
            now = time.time()
            record = {
                'id': test.id(), 'outcome': outcome, 'detail': detail, 'subtest': subtest,
                'time': now, 'origin': os.environ['PLATFORM_BROWSER_URL'],
                'requests_failed': state.get('requests_failed', []),
                'script_http_errors': state.get('script_http_errors', []),
                'console_errors': state.get('console_errors', []),
                'page_errors': state.get('page_errors', []),
                'pending_requests': [
                    {**row, 'age': now - row['started']}
                    for row in state.get('pending', {}).values()
                ],
            }
            with result_file.open('a') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + chr(10))

        def addSuccess(self, test):
            super().addSuccess(test)
            self.record(test, 'passed')

        def addFailure(self, test, error):
            super().addFailure(test, error)
            self.record(test, 'failed', self._exc_info_to_string(error, test))

        def addError(self, test, error):
            super().addError(test, error)
            self.record(test, 'error', self._exc_info_to_string(error, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.record(test, 'skipped', reason)

        def addSubTest(self, test, subtest, error):
            super().addSubTest(test, subtest, error)
            if error:
                self.record(test, 'failed', self._exc_info_to_string(error, test), str(subtest))

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for case in cases:
        suite.addTests(loader.loadTestsFromName(case['id']))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Results).run(suite)
    return int(not result.wasSuccessful())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.environ.get('PLATFORM_BROWSER_URL'))
    parser.add_argument('--source-url')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--tests-file', type=Path)
    parser.add_argument('--case-timeout', type=float)
    parser.add_argument('--cdp-url')
    parser.add_argument('--executable-path')
    parser.add_argument('--map-host')
    parser.add_argument('--host-memory-status', type=Path)
    parser.add_argument('--batch')
    parser.add_argument('--worker-index', type=int, default=0)
    args = parser.parse_args()
    if not args.base_url:
        parser.error('--base-url is required')
    args.source_url = args.source_url or args.base_url
    args.output.mkdir(parents=True, exist_ok=True)
    if args.batch:
        return run_worker(args)
    if not 1 <= args.workers <= 4 or (args.cdp_url and args.workers != 1):
        parser.error('Use 1-4 workers; native CDP browser runs require one worker')
    cases = discover()
    if args.tests_file:
        requested = set(json.loads(args.tests_file.read_text()))
        cases = [case for case in cases if case['id'] in requested]
        if len(cases) != len(requested):
            parser.error('Unknown or duplicated selected test cases')
    if not cases:
        parser.error('No tests selected')
    (args.output / 'manifest.json').write_text(json.dumps({
        'total': len(cases), 'base_url': args.base_url, 'source_url': args.source_url,
        'workers': args.workers, 'cases': cases, 'automatic_retries': False,
    }, ensure_ascii=False, indent=2))
    batches = [cases[index::args.workers] for index in range(args.workers)]
    processes = []
    streams = []
    try:
        for index, batch in enumerate(batches):
            data = args.output / f'worker-{index}.json'
            data.write_text(json.dumps(batch))
            stream = (args.output / f'worker-{index}.log').open('w')
            streams.append(stream)
            command = [
                sys.executable, str(Path(__file__).resolve()), '--base-url', args.base_url,
                '--source-url', args.source_url, '--output', str(args.output),
                '--batch', str(data), '--worker-index', str(index),
            ]
            if args.case_timeout is not None:
                command += ['--case-timeout', str(args.case_timeout)]
            if args.cdp_url:
                command += ['--cdp-url', args.cdp_url]
            if args.executable_path:
                command += ['--executable-path', args.executable_path]
            if args.map_host:
                command += ['--map-host', args.map_host]
            if args.host_memory_status:
                command += ['--host-memory-status', str(args.host_memory_status)]
            processes.append(subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT))
        statuses = [process.wait() for process in processes]
        return int(any(statuses))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in streams:
            stream.close()


if __name__ == '__main__':
    raise SystemExit(main())
