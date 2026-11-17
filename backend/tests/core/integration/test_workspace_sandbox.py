"""Opt-in tests against an independent real OpenSandbox manager and office image."""

import asyncio
import json
import os
from uuid import uuid4

import pytest

from zhiheng.infrastructure.sandbox.adapters.opensandbox import OpenSandboxFactory
from zhiheng.infrastructure.sandbox.contracts import SandboxInput
from zhiheng.shared.errors import AppError


@pytest.fixture
def sandbox_factory() -> OpenSandboxFactory:
    url = os.getenv('ZHIHENG_TEST_SANDBOX_URL', '')
    key = os.getenv('ZHIHENG_TEST_SANDBOX_API_KEY', '')
    if not url or not key:
        pytest.skip('A separate real OpenSandbox manager is required')
    return OpenSandboxFactory(url, key, os.getenv('ZHIHENG_TEST_SANDBOX_IMAGE', 'zhiheng-workspace-runtime:0.1.0'))


async def test_real_office_files_preview_and_checkpoint_restore(sandbox_factory: OpenSandboxFactory) -> None:
    inputs = (SandboxInput('inputs/source/data.csv', b'amount\n2\n3\n'),)
    first = await sandbox_factory.create(uuid4(), uuid4(), inputs=inputs)
    try:
        code = """
import csv
from openpyxl import Workbook
from docx import Document
with open('inputs/source/data.csv') as source:
    total = sum(int(row['amount']) for row in csv.DictReader(source))
workbook = Workbook()
workbook.active.append(['total'])
workbook.active.append([total])
workbook.save('outputs/summary.xlsx')
document = Document()
document.add_heading('经营汇总', 0)
document.add_paragraph(str(total))
document.save('outputs/report.docx')
print(total)
"""
        result = await first.execute(code, packages=('openpyxl==3.1.5', 'python-docx==1.2.0'))
        assert result.exit_code == 0 and result.stdout.strip() == '5'
        artifacts = await first.collect_outputs()
        assert len(artifacts) == 2 and all(item.validation['valid'] for item in artifacts)
        assert all(item.previews and item.previews[0].content.startswith(b'%PDF') for item in artifacts)
        snapshots = await first.snapshot_files()
        assert all(item.path.startswith(('working/', 'outputs/')) for item in snapshots)
        assert not any('.previews-' in item.path for item in snapshots)
        original = {item.path: item.sha256 for item in artifacts}
    finally:
        await first.close()
    second = await sandbox_factory.create(uuid4(), uuid4(), inputs=inputs, initial_files=snapshots)
    try:
        result = await second.execute(
            "from docx import Document; d=Document('outputs/report.docx'); d.paragraphs[0].text='新标题'; d.save('outputs/report.docx')"
        )
        assert result.exit_code == 0
        modified = {item.path: item.sha256 for item in await second.collect_outputs()}
        assert modified['outputs/summary.xlsx'] == original['outputs/summary.xlsx']
        assert modified['outputs/report.docx'] != original['outputs/report.docx']
    finally:
        await second.close()


async def test_real_isolation_readonly_inputs_filesystem_network_and_limits(
    sandbox_factory: OpenSandboxFactory,
) -> None:
    session = await sandbox_factory.create(
        uuid4(), uuid4(), inputs=(SandboxInput('inputs/source/data.csv', b'value\n1\n'),)
    )
    try:
        result = await session.execute("""
import json, os, socket
def forbidden(path):
    try:
        open(path,'w').write('bad')
    except OSError:
        return True
    return False
def blocked(host,port):
    with socket.socket() as connection:
        connection.settimeout(.2)
        return connection.connect_ex((host,port)) != 0
print(json.dumps({'uid':os.getuid(),
 'caps':int(next(line.split()[1] for line in open('/proc/self/status') if line.startswith('CapEff:')),16),
 'input_ro':forbidden('inputs/source/data.csv'), 'root_ro':forbidden('/opt/zhiheng/escape'),
 'execd_blocked':blocked('127.0.0.1',44772), 'external_blocked':blocked('1.1.1.1',443),
 'scratch':os.statvfs('/workspace').f_blocks*os.statvfs('/workspace').f_frsize,
 'tmp':os.statvfs('/tmp').f_blocks*os.statvfs('/tmp').f_frsize}))
""")
        assert result.exit_code == 0, result.stderr
        facts = json.loads(result.stdout)
        assert facts['uid'] == 10001 and facts['caps'] == 0
        assert facts['input_ro'] and facts['root_ro'] and facts['execd_blocked'] and facts['external_blocked']
        assert facts['scratch'] <= 1024**3 and facts['tmp'] <= 128 * 1024**2
        await session.add_input(SandboxInput('inputs/history/old.txt', b'preserved'))
        assert await session.read_file('inputs/history/old.txt') == 'preserved'
        with pytest.raises(AppError):
            await session.add_input(SandboxInput('inputs/history/old.txt', b'changed'))
        assert await session.read_file('inputs/history/old.txt') == 'preserved'
        timed_out = await session.execute('import time; time.sleep(5)', timeout=1)
        assert timed_out.timed_out and timed_out.exit_code == 124
        assert (await session.execute('print(42)')).stdout.strip() == '42'
        await session.execute("from pathlib import Path; Path('outputs/trap').symlink_to('/etc/passwd')")
        with pytest.raises(AppError):
            await session.collect_outputs()
    finally:
        await session.close()


async def test_real_cancellation_destroys_the_execution_environment(sandbox_factory: OpenSandboxFactory) -> None:
    session = await sandbox_factory.create(uuid4(), uuid4())
    task = asyncio.create_task(session.execute('import time; time.sleep(60)', timeout=120))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.close()


async def test_model_modules_do_not_shadow_trusted_outer_helpers(sandbox_factory: OpenSandboxFactory) -> None:
    session = await sandbox_factory.create(uuid4(), uuid4())
    try:
        result = await session.execute("""
from pathlib import Path
payload = "open('/workspace/working/shadow-imported', 'w').write('unsafe')\\nraise RuntimeError('shadowed trusted helper')\\n"
for name in ('json.py', 'sitecustomize.py', 'subprocess.py'):
    Path('/workspace', name).write_text(payload)
""")
        assert result.exit_code == 0
        await session.write_file('working/safe.txt', 'verified')
        assert await session.read_file('working/safe.txt') == 'verified'
        await session.collect_outputs()
        snapshots = await session.snapshot_files()
        assert {item.path for item in snapshots} == {'working/safe.txt'}
    finally:
        await session.close()


async def test_binary_dependency_is_installed_inside_the_isolated_task(sandbox_factory: OpenSandboxFactory) -> None:
    session = await sandbox_factory.create(uuid4(), uuid4())
    try:
        result = await session.execute(
            'import humanize; print(humanize.intcomma(1234))', packages=('humanize==4.13.0',)
        )
        assert result.exit_code == 0 and result.stdout.strip() == '1,234', result.stderr
        assert any(item.lower() == 'humanize==4.13.0' for item in result.environment)
        repeated = await session.execute(
            'import humanize; print(humanize.intcomma(5678))', packages=('humanize==4.13.0',)
        )
        assert repeated.exit_code == 0 and repeated.stdout.strip() == '5,678'
    finally:
        await session.close()
