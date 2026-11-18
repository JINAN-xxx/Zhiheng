import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zhiheng.application.work.execution import ExecutionProgress
from zhiheng.application.work.tools import EMPTY_SHA256, WorkspaceTools
from zhiheng.infrastructure.sandbox import SandboxCommandResult, SandboxFile, SandboxInput
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.work.agent import WorkAgentService


def make_tools(cached: object = None) -> WorkspaceTools:
    principal = Principal(uuid4(), 'owner', False)
    claim = ClaimedRun(uuid4(), 'work_agent', uuid4(), principal.id, uuid4(), {})
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value = object()
    sessions.begin.return_value.__aenter__.return_value = object()
    context = SimpleNamespace(
        database=SimpleNamespace(sessions=sessions),
        tasks=SimpleNamespace(
            cached_step=AsyncMock(return_value=cached),
            record_step=AsyncMock(),
            latest_workspace_files=AsyncMock(return_value=None),
        ),
        policy=SimpleNamespace(tool_calls=24),
    )
    service = SimpleNamespace(context=context, guard=AsyncMock(return_value=principal))
    return WorkspaceTools(cast('WorkAgentService', service), claim, principal, ExecutionProgress(), AsyncMock())


def workspace_state(
    sequence: int,
    files: list[dict[str, object]],
    *,
    failed: tuple[str, ...] = (),
    run_id: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(sequence=sequence, files=files, failed_operations=list(failed), run_id=run_id or uuid4())


@pytest.mark.asyncio
async def test_cached_tool_rejects_changed_arguments_without_replaying() -> None:
    cached = SimpleNamespace(kind='tool', name='write_file', details={'path': 'working/a.txt', 'content': 'old'})
    tools = make_tools(cached)
    with pytest.raises(AppError, match='不同参数') as error:
        await tools.invoke('write_file', {'path': 'working/a.txt', 'content': 'new'}, 'action')
    assert error.value.code == 'idempotency_conflict'
    tools.context.tasks.record_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_plan_restores_progress_and_limits_model_preview() -> None:
    todos = [{'content': '检查资料', 'status': 'pending'}]
    arguments = {'todos': todos}
    cached = SimpleNamespace(
        kind='tool',
        name='write_todos',
        details=WorkspaceTools._tool_details(arguments),
        status='succeeded',
        result={'success': True, 'data': {'todos': todos}, '_workspace_files': []},
    )
    tools = make_tools(cached)
    # A long tool result is paged, while the separately validated plan remains bounded.
    cached.result['data']['additional'] = '内容' * 6000
    result = await tools.invoke('write_todos', arguments, 'plan')
    assert tools.progress.plan == ({'content': '检查资料', 'status': 'pending'},)
    assert result['data']['full_message_id'] == 'plan'
    assert len(result['data']['preview']) == 6000
    assert '_workspace_files' not in result
    tools.context.tasks.record_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_staging_retries_after_remote_add_failure() -> None:
    tools = make_tools()
    identifier = uuid4()
    info = SimpleNamespace(original_filename='source.csv')
    tools.context.queries = SimpleNamespace(read=AsyncMock(return_value=(info, b'column\nvalue\n')))
    tools.session = SimpleNamespace(add_input=AsyncMock(side_effect=[AppError('failed', '重试', 503), None]))
    detail = SimpleNamespace(inputs=[SimpleNamespace(file_id=identifier)], artifacts=[])
    with patch.object(tools, '_detail', AsyncMock(return_value=detail)):
        with pytest.raises(AppError):
            await tools._file(str(identifier))
        assert tools.staged == {}
        await tools._file(str(identifier))
    assert tools.session.add_input.await_count == 2
    assert list(tools.staged) == [f'inputs/{identifier.hex}/source.csv']


@pytest.mark.asyncio
async def test_executed_code_with_failed_checkpoint_is_unknown_and_not_replayed() -> None:
    tools = make_tools()
    sandbox = SimpleNamespace(execute=AsyncMock(return_value=SandboxCommandResult(0, 'done', '')))
    with (
        patch.object(tools, '_sandbox', AsyncMock(return_value=sandbox)),
        patch.object(tools, '_snapshot', AsyncMock(side_effect=AppError('storage_unavailable', '保存暂不可用', 503))),
        pytest.raises(AppError) as error,
    ):
        await tools.invoke('run_python', {'code': 'create_file()'}, 'code')
    assert error.value.code == 'work_execution_uncertain'
    statuses = [call.kwargs['status'] for call in tools.context.tasks.record_step.await_args_list]
    assert statuses == ['running', 'unknown']
    assert tools.blocking


@pytest.mark.asyncio
async def test_known_failed_command_preserves_partial_files() -> None:
    tools = make_tools()
    sandbox = SimpleNamespace(execute=AsyncMock(return_value=SandboxCommandResult(1, '', 'bad data')))
    with (
        patch.object(tools, '_sandbox', AsyncMock(return_value=sandbox)),
        patch.object(tools, '_snapshot', AsyncMock()) as snapshot,
    ):
        result = await tools.invoke('run_python', {'code': 'partial_then_fail()'}, 'code')
    assert result['success'] is False
    assert 'text_escaping' not in result
    snapshot.assert_awaited_once()
    assert tools.context.tasks.record_step.await_args.kwargs['status'] == 'failed'


@pytest.mark.asyncio
async def test_empty_checkpoint_files_use_manifest_without_managed_empty_upload() -> None:
    tools = make_tools()
    tools.session = SimpleNamespace(
        snapshot_files=AsyncMock(
            return_value=(
                SandboxInput('working/marker', b''),
                SandboxInput('working/notes.txt', b'notes'),
            )
        )
    )
    identifier = uuid4()
    with patch.object(
        tools, '_register', AsyncMock(return_value=SimpleNamespace(id=identifier, sha256='hash'))
    ) as register:
        await tools._snapshot()
    register.assert_awaited_once_with('working/notes.txt', b'notes', 'work_checkpoint')
    assert tools.workspace_files == [
        {'path': 'working/marker', 'empty': True, 'sha256': EMPTY_SHA256},
        {'path': 'working/notes.txt', 'file_id': str(identifier), 'sha256': 'hash'},
    ]


def prepare_factory(tools: WorkspaceTools) -> SimpleNamespace:
    lease = SimpleNamespace(id=uuid4())
    sandbox = SimpleNamespace(id='sandbox-123', close=AsyncMock())
    tools.context.policy.sandbox_image = 'runtime:test'
    tools.context.policy.run_seconds = 60
    tools.context.runs = SimpleNamespace(lock_request=AsyncMock())
    tools.context.tasks.sandbox_leases = AsyncMock(return_value=[])
    tools.context.tasks.reserve_sandbox = AsyncMock(return_value=lease)
    tools.context.tasks.bind_sandbox = AsyncMock()
    tools.context.tasks.close_sandbox = AsyncMock()
    tools.context.sandbox = SimpleNamespace(check=AsyncMock(return_value=True), create=AsyncMock(return_value=sandbox))
    return sandbox


@pytest.mark.asyncio
async def test_empty_checkpoint_restores_without_a_file_read() -> None:
    tools = make_tools()
    sandbox = prepare_factory(tools)
    tools.workspace_files = [{'path': 'working/marker', 'empty': True, 'sha256': EMPTY_SHA256}]
    with patch.object(tools, '_detail', AsyncMock(return_value=SimpleNamespace(inputs=[]))):
        assert await tools._sandbox() is sandbox
    assert tools.context.sandbox.create.await_args.kwargs['initial_files'] == (SandboxInput('working/marker', b''),)


@pytest.mark.asyncio
async def test_late_bind_failure_destroys_container_and_records_actual_identity() -> None:
    tools = make_tools()
    sandbox = prepare_factory(tools)
    tools.context.tasks.bind_sandbox.side_effect = AppError('execution_lost', '执行权已丢失', 409)
    with (
        patch.object(tools, '_detail', AsyncMock(return_value=SimpleNamespace(inputs=[]))),
        pytest.raises(AppError),
    ):
        await tools._sandbox()
    sandbox.close.assert_awaited_once()
    cleanup = tools.context.tasks.close_sandbox.await_args.kwargs
    assert cleanup['status'] == 'closed' and cleanup['sandbox_id'] == sandbox.id


@pytest.mark.asyncio
async def test_artifact_and_preview_share_one_registration_transaction() -> None:
    tools = make_tools()
    tools.context.files = SimpleNamespace(
        storage=SimpleNamespace(put=AsyncMock(side_effect=['original-key', 'preview-key']))
    )
    registrations = MagicMock()
    native_session = object()
    registrations.return_value.__aenter__.return_value = native_session
    tools.context.objects = SimpleNamespace(registrations=registrations)
    tools.context.runs = SimpleNamespace(lock_execution=AsyncMock())
    tools.context.tasks.record_artifact = AsyncMock(return_value='artifact')
    info, preview = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    item = SandboxFile(
        'outputs/report.docx',
        b'word',
        'hash',
        'application/octet-stream',
        {'valid': True},
        (SandboxInput('outputs/report.pdf', b'pdf'),),
    )
    with patch.object(tools, '_create_file', AsyncMock(side_effect=[info, preview])) as create:
        assert await tools._store_artifact(item) == 'artifact'
    registrations.assert_called_once_with(('original-key', 'preview-key'))
    assert all(call.args[0] is native_session for call in create.await_args_list)
    assert tools.context.tasks.record_artifact.await_args.args[0] is native_session
    assert tools.context.tasks.record_artifact.await_args.kwargs['preview_file_id'] == preview.id


@pytest.mark.asyncio
async def test_preview_upload_failure_compensates_only_unregistered_original_key() -> None:
    tools = make_tools()
    tools.context.files = SimpleNamespace(
        storage=SimpleNamespace(put=AsyncMock(side_effect=['fresh-key', AppError('storage_error', '上传失败', 503)]))
    )
    tools.context.objects = SimpleNamespace(discard_unregistered=AsyncMock(), registrations=MagicMock())
    item = SandboxFile(
        'outputs/report.docx',
        b'word',
        'hash',
        'application/octet-stream',
        {'valid': True},
        (SandboxInput('outputs/report.pdf', b'pdf'),),
    )
    with pytest.raises(AppError):
        await tools._store_artifact(item)
    tools.context.objects.discard_unregistered.assert_awaited_once_with('fresh-key')
    tools.context.objects.registrations.assert_not_called()


@pytest.mark.asyncio
async def test_empty_formal_output_is_rejected_while_valid_output_is_preserved() -> None:
    tools = make_tools()
    empty = SandboxFile('outputs/empty.txt', b'', EMPTY_SHA256, 'text/plain', {'valid': False})
    valid = SandboxFile('outputs/report.txt', b'valid report', 'hash', 'text/plain', {'valid': True})
    artifact = SimpleNamespace(validation={'valid': True}, model_dump=MagicMock(return_value={'name': 'report.txt'}))
    sandbox = SimpleNamespace(collect_outputs=AsyncMock(return_value=(empty, valid)))
    with (
        patch.object(tools, '_sandbox', AsyncMock(return_value=sandbox)),
        patch.object(tools, '_detail', AsyncMock(return_value=SimpleNamespace(artifacts=[]))),
        patch.object(tools, '_store_artifact', AsyncMock(return_value=artifact)) as store,
    ):
        result = await tools._publish()
    store.assert_awaited_once_with(valid)
    assert result['success'] is False and tools.blocking
    assert result['data']['artifacts'] == [{'name': 'report.txt'}]
    assert result['data']['rejected'][0]['path'] == 'outputs/empty.txt'


@pytest.mark.asyncio
async def test_stdout_controls_are_visible_and_checkpoint_binary_bytes_are_unchanged() -> None:
    tools = make_tools()
    binary = b'\x00\xff\x01\r\n\\u0000'
    sandbox = SimpleNamespace(
        execute=AsyncMock(return_value=SandboxCommandResult(0, 'binary\x00output\x01\ud800\n', '')),
        snapshot_files=AsyncMock(return_value=(SandboxInput('working/raw.bin', binary),)),
    )
    tools.session = sandbox
    info = SimpleNamespace(id=uuid4(), sha256=hashlib.sha256(binary).hexdigest())
    with patch.object(tools, '_register', AsyncMock(return_value=info)) as register:
        result = await tools.invoke('run_python', {'code': 'print(chr(0))'}, 'binary-stdout')
    register.assert_awaited_once_with('working/raw.bin', binary, 'work_checkpoint')
    assert result['data']['stdout'] == 'binary\\u0000output\\u0001\\ud800\n'
    assert result.get('text_escaping')
    record = tools.context.tasks.record_step.await_args.kwargs['result']
    assert record['data']['stdout'] == result['data']['stdout']


@pytest.mark.asyncio
async def test_text_write_executes_original_nul_content_and_cached_arguments_remain_equivalent() -> None:
    tools = make_tools()
    content = 'literal\\u0000 and actual\x00'
    binary = content.encode()
    sandbox = SimpleNamespace(
        write_file=AsyncMock(),
        snapshot_files=AsyncMock(return_value=(SandboxInput('working/raw.txt', binary),)),
    )
    tools.session = sandbox
    arguments = {'path': 'working/raw.txt', 'content': content}
    info = SimpleNamespace(id=uuid4(), sha256=hashlib.sha256(binary).hexdigest())
    with patch.object(tools, '_register', AsyncMock(return_value=info)) as register:
        result = await tools.invoke('write_file', arguments, 'nul-content')
    sandbox.write_file.assert_awaited_once_with('working/raw.txt', content)
    register.assert_awaited_once_with('working/raw.txt', binary, 'work_checkpoint')
    record = tools.context.tasks.record_step.await_args.kwargs
    assert '\x00' not in record['details']['arguments']['content']
    assert record['details']['arguments']['content'] != content
    tools.context.tasks.cached_step.return_value = SimpleNamespace(
        kind='tool',
        name='write_file',
        details=record['details'],
        status='succeeded',
        result=record['result'],
    )
    assert await tools.invoke('write_file', arguments, 'nul-content') == result
    sandbox.write_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_literal_unicode_escape_cannot_reuse_execution_of_actual_nul_argument() -> None:
    original = {'path': 'working/raw.txt', 'content': '\x00'}
    literal = {'path': 'working/raw.txt', 'content': '\\u0000'}
    original_details = WorkspaceTools._tool_details(original)
    literal_details = WorkspaceTools._tool_details(literal)
    assert original_details['arguments'] == literal_details['arguments']
    assert original_details['arguments_fingerprint'] != literal_details['arguments_fingerprint']
    cached = SimpleNamespace(
        kind='tool',
        name='write_file',
        details=original_details,
        status='succeeded',
        result={'success': True, 'data': {'path': 'working/raw.txt'}},
    )
    tools = make_tools(cached)
    with pytest.raises(AppError) as error:
        await tools.invoke('write_file', literal, 'same-action')
    assert error.value.code == 'idempotency_conflict'
    tools.context.tasks.record_step.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('order', [('first', 'second'), ('second', 'first')])
async def test_cached_batch_replay_never_rewinds_latest_confirmed_workspace(order: tuple[str, str]) -> None:
    tools = make_tools()
    older = [{'path': 'working/first.txt', 'file_id': str(uuid4()), 'sha256': 'first'}]
    latest = [*older, {'path': 'working/second.txt', 'file_id': str(uuid4()), 'sha256': 'second'}]
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(2, latest)
    arguments = {'path': 'working/first.txt'}
    steps = {
        'first': SimpleNamespace(
            kind='tool',
            name='read_file',
            details=WorkspaceTools._tool_details(arguments),
            status='succeeded',
            result={'success': True, 'data': {'content': 'old'}, '_workspace_files': older, '_workspace_sequence': 1},
        ),
        'second': SimpleNamespace(
            kind='tool',
            name='read_file',
            details=WorkspaceTools._tool_details(arguments),
            status='succeeded',
            result={'success': True, 'data': {'content': 'new'}, '_workspace_files': latest, '_workspace_sequence': 2},
        ),
    }
    tools.context.tasks.cached_step.side_effect = [steps[name] for name in order]
    for name in order:
        result = await tools.invoke('read_file', arguments, name)
        assert tools.workspace_files == latest and tools._workspace_sequence == 2
        assert '_workspace_sequence' not in result and '_workspace_files' not in result
    tools.context.tasks.latest_workspace_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_cached_old_step_cannot_rewind_a_new_mutation_after_restore() -> None:
    tools = make_tools()
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(7, [])
    arguments = {'path': 'working/current.txt', 'content': 'current'}
    sandbox = SimpleNamespace(
        write_file=AsyncMock(),
        snapshot_files=AsyncMock(return_value=(SandboxInput('working/current.txt', b'current'),)),
    )
    tools.session = sandbox
    info = SimpleNamespace(id=uuid4(), sha256=hashlib.sha256(b'current').hexdigest())
    cached_arguments = {'path': 'working/old.txt'}
    cached = SimpleNamespace(
        kind='tool',
        name='read_file',
        details=WorkspaceTools._tool_details(cached_arguments),
        status='succeeded',
        result={
            'success': True,
            'data': {'content': 'old'},
            '_workspace_sequence': 2,
            '_workspace_files': [{'path': 'working/old.txt', 'file_id': str(uuid4()), 'sha256': 'old'}],
        },
    )
    tools.context.tasks.cached_step.side_effect = [None, cached]
    with patch.object(tools, '_register', AsyncMock(return_value=info)):
        await tools.invoke('write_file', arguments, 'new-mutation')
    current = list(tools.workspace_files)
    assert tools._workspace_sequence == 8
    stored = tools.context.tasks.record_step.await_args.kwargs['result']
    assert stored['_workspace_sequence'] == 8
    await tools.invoke('read_file', cached_arguments, 'old-cached-read')
    assert tools.workspace_files == current and tools._workspace_sequence == 8


@pytest.mark.asyncio
async def test_successful_publication_after_repair_clears_only_publication_failure() -> None:
    tools = make_tools()
    empty = SandboxFile('outputs/report.txt', b'', EMPTY_SHA256, 'text/plain', {'valid': False})
    fixed = SandboxFile('outputs/report.txt', b'fixed', 'hash', 'text/plain', {'valid': True})
    artifact = SimpleNamespace(validation={'valid': True}, model_dump=MagicMock(return_value={'name': 'report.txt'}))
    sandbox = SimpleNamespace(
        collect_outputs=AsyncMock(side_effect=[(empty,), (fixed,)]),
        write_file=AsyncMock(),
        snapshot_files=AsyncMock(return_value=(SandboxInput('outputs/report.txt', b'fixed'),)),
    )
    tools.session = sandbox
    info = SimpleNamespace(id=uuid4(), sha256=hashlib.sha256(b'fixed').hexdigest())
    with (
        patch.object(tools, '_detail', AsyncMock(return_value=SimpleNamespace(artifacts=[]))),
        patch.object(tools, '_register', AsyncMock(return_value=info)),
        patch.object(tools, '_store_artifact', AsyncMock(return_value=artifact)),
    ):
        failed = await tools.invoke('publish_outputs', {}, 'empty')
        assert failed['success'] is False and tools.blocking
        await tools.invoke('write_file', {'path': fixed.path, 'content': 'fixed'}, 'repair')
        assert tools.blocking
        published = await tools.invoke('publish_outputs', {}, 'fixed')
    assert published['success'] is True and not tools.blocking


@pytest.mark.asyncio
async def test_valid_publication_does_not_hide_an_unresolved_command_failure() -> None:
    tools = make_tools()
    valid = SandboxFile('outputs/report.txt', b'old valid output', 'hash', 'text/plain', {'valid': True})
    artifact = SimpleNamespace(validation={'valid': True}, model_dump=MagicMock(return_value={'name': 'report.txt'}))
    tools.session = SimpleNamespace(
        execute=AsyncMock(return_value=SandboxCommandResult(1, '', 'incomplete')),
        collect_outputs=AsyncMock(return_value=(valid,)),
    )
    with (
        patch.object(tools, '_snapshot', AsyncMock()),
        patch.object(tools, '_detail', AsyncMock(return_value=SimpleNamespace(artifacts=[]))),
        patch.object(tools, '_store_artifact', AsyncMock(return_value=artifact)),
    ):
        failed = await tools.invoke('run_python', {'code': 'failed()'}, 'failed-execution')
        assert failed['success'] is False
        published = await tools.invoke('publish_outputs', {}, 'existing-artifact')
    assert published['success'] is True and tools.blocking


@pytest.mark.asyncio
async def test_finalize_restores_latest_outputs_even_without_a_tool_callback() -> None:
    tools = make_tools()
    tools.service.guard.return_value = Principal(tools.principal.id, 'owner', True)
    files = [{'path': 'outputs/report.txt', 'file_id': str(uuid4()), 'sha256': 'report'}]
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(9, files)
    with patch.object(tools, '_publish', AsyncMock(return_value={'success': True})) as publish:
        await tools.finalize_outputs()
    publish.assert_awaited_once_with(required=False)
    assert tools.workspace_files == files and tools._workspace_sequence == 9
    tools.context.tasks.latest_workspace_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_without_outputs_keeps_greeting_free_of_sandbox() -> None:
    tools = make_tools()
    with patch.object(tools, '_publish', AsyncMock()) as publish:
        await tools.finalize_outputs()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_rejects_control_characters_in_paths_before_registration() -> None:
    tools = make_tools()
    tools.session = SimpleNamespace(
        snapshot_files=AsyncMock(return_value=(SandboxInput('working/a\x01.txt', b'unchanged'),))
    )
    with patch.object(tools, '_register', AsyncMock()) as register, pytest.raises(AppError) as error:
        await tools._snapshot()
    assert error.value.code == 'sandbox_invalid_path'
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_failed_command_with_side_effects_is_not_reexecuted_on_resume() -> None:
    first = make_tools()
    appended = []

    async def execute(*_args: object, **_kwargs: object) -> SandboxCommandResult:
        appended.append(b'event\n')
        return SandboxCommandResult(1, '', 'failed after append')

    sandbox = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        snapshot_files=AsyncMock(side_effect=lambda: (SandboxInput('working/events.txt', b''.join(appended)),)),
    )
    first.session = sandbox
    arguments = {'code': 'append_then_fail()'}
    info = SimpleNamespace(id=uuid4(), sha256=hashlib.sha256(b'event\n').hexdigest())
    with patch.object(first, '_register', AsyncMock(return_value=info)) as register:
        outcome = await first.invoke('run_python', arguments, 'append-once')
    saved = first.context.tasks.record_step.await_args.kwargs
    assert saved['status'] == 'failed' and saved['result']['_workspace_failures'] == ['execution']
    cached = SimpleNamespace(
        kind='tool', name='run_python', details=saved['details'], status='failed', result=saved['result']
    )
    resumed = make_tools(cached)
    resumed.claim.payload['resume'] = True
    resumed.context.tasks.latest_workspace_files.return_value = workspace_state(
        saved['result']['_workspace_sequence'],
        saved['result']['_workspace_files'],
        failed=('execution',),
        run_id=first.claim.id,
    )
    resumed.session = sandbox
    assert await resumed.invoke('run_python', arguments, 'append-once') == outcome
    assert appended == [b'event\n'] and resumed.blocking
    sandbox.execute.assert_awaited_once()
    register.assert_awaited_once()
    resumed.context.tasks.record_step.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('order', [('failure', 'repair'), ('repair', 'failure')])
async def test_old_failed_cache_does_not_reblock_latest_repaired_state(order: tuple[str, str]) -> None:
    tools = make_tools()
    tools.claim.payload['resume'] = True
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(2, [], run_id=tools.claim.id)
    arguments = {'code': 'confirmed()'}
    steps = {
        'failure': SimpleNamespace(
            kind='tool',
            name='run_python',
            details=WorkspaceTools._tool_details(arguments),
            status='failed',
            result={
                'success': False,
                'error': {'code': 'command_failed'},
                '_workspace_files': [],
                '_workspace_sequence': 1,
                '_workspace_failures': ['execution'],
            },
        ),
        'repair': SimpleNamespace(
            kind='tool',
            name='run_python',
            details=WorkspaceTools._tool_details(arguments),
            status='succeeded',
            result={
                'success': True,
                'data': {},
                '_workspace_files': [],
                '_workspace_sequence': 2,
                '_workspace_failures': [],
            },
        ),
    }
    tools.context.tasks.cached_step.side_effect = [steps[name] for name in order]
    with patch.object(tools, '_dispatch', AsyncMock()) as dispatch:
        for name in order:
            await tools.invoke('run_python', arguments, name)
            assert not tools.blocking and tools._workspace_sequence == 2
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('resume', [False, True])
async def test_zero_tool_resume_keeps_unresolved_failures_and_new_turn_clears_them(resume: bool) -> None:
    tools = make_tools()
    tools.claim.payload.update({'resume': resume})
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(5, [], failed=('execution',))
    with patch.object(tools, '_publish', AsyncMock()) as publish:
        await tools.finalize_outputs()
    assert tools.blocking is resume
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_read_only_conversation_does_not_start_sandbox_for_old_outputs() -> None:
    tools = make_tools()
    files = [{'path': 'outputs/report.txt', 'file_id': str(uuid4()), 'sha256': 'report'}]
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(9, files)
    with patch.object(tools, '_publish', AsyncMock()) as publish:
        await tools.finalize_outputs()
    publish.assert_not_awaited()
    tools.service.guard.assert_awaited_once_with(tools.claim)
