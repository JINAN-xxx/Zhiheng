from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from zhiheng.api.inference.streaming import terminal_error
from zhiheng.application.execution import timeouts
from zhiheng.application.execution.executor import InferenceExecutor, InferenceProgress, retrieval_execution_error
from zhiheng.application.execution.timeouts import ModelExecutionClock, ModelExecutionTimeout
from zhiheng.modules.models.adapters.agent_tools import _Response
from zhiheng.modules.models.public import ModelDelta, ProviderError
from zhiheng.runtime.contracts import ClaimedRun, RunError, RunSnapshot
from zhiheng.shared.time import utcnow


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> tuple[ModelExecutionClock, list[float]]:
    current = [100.0]
    monkeypatch.setattr(timeouts, 'monotonic', lambda: current[0])
    monkeypatch.setattr(timeouts, 'utcnow', lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    value = ModelExecutionClock(2, 3, 20, started_at=current[0])
    value.provider_started_at = current[0]
    return value, current


def test_reasoning_and_tool_fragments_keep_a_stream_alive_beyond_the_first_response_budget(
    clock: tuple[ModelExecutionClock, list[float]],
) -> None:
    timer, current = clock
    progress = InferenceProgress(clock=timer)
    for delta in [ModelDelta(reasoning='推理'), ModelDelta(activity=True), ModelDelta(text='正文')]:
        current[0] += 1.5
        progress.append(delta, agent_execution=False)
        timer.check()
    assert current[0] - timer.started_at > timer.first_response_seconds
    assert progress.text == '正文' and progress.reasoning == '推理'


def test_empty_heartbeats_and_usage_counters_do_not_extend_the_first_response_budget(
    clock: tuple[ModelExecutionClock, list[float]],
) -> None:
    timer, current = clock
    progress = InferenceProgress(clock=timer)
    for delta in [ModelDelta(), ModelDelta(input_tokens=8)]:
        current[0] += 1
        progress.append(delta, agent_execution=False)
    with pytest.raises(ModelExecutionTimeout) as caught:
        timer.check()
    assert caught.value.phase == 'first_response'
    assert progress.input_tokens == 8


@pytest.mark.parametrize('completed,phase', [(False, 'idle'), (True, 'finalize')])
def test_silent_stream_and_stalled_checkpoint_have_distinct_reasons(
    clock: tuple[ModelExecutionClock, list[float]], completed: bool, phase: str
) -> None:
    timer, current = clock
    timer.activity()
    current[0] += 3
    with pytest.raises(ModelExecutionTimeout) as caught:
        timer.check(completed=completed)
    assert caught.value.phase == phase


def test_continuous_output_cannot_extend_the_absolute_budget(clock: tuple[ModelExecutionClock, list[float]]) -> None:
    timer, current = clock
    for _ in range(19):
        current[0] += 1
        timer.activity()
        timer.check()
    current[0] += 1
    timer.activity()
    with pytest.raises(ModelExecutionTimeout) as caught:
        timer.check()
    assert caught.value.phase == 'total'


def test_nested_wait_uses_the_same_absolute_deadline_instead_of_a_generic_failure(
    clock: tuple[ModelExecutionClock, list[float]],
) -> None:
    timer, current = clock
    current[0] += 20
    claim = ClaimedRun(uuid4(), 'chat', uuid4(), uuid4(), uuid4(), {})
    progress = InferenceProgress(clock=timer, provider_started=True)
    status, error = InferenceExecutor._execution_failure(claim, TimeoutError('private-error-text'), progress)
    assert status == 'interrupted' and error.code == 'model_deadline_exceeded'
    assert 'private-error-text' not in error.message


def test_child_budget_is_capped_by_its_parent_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(timeouts, 'utcnow', lambda: now)
    value = ModelExecutionClock(120, 90, 600, now + timedelta(seconds=12), started_at=100)
    assert value.deadline == 112 and value.deadline_at == now + timedelta(seconds=12)


def test_tool_argument_progress_is_visible_before_a_complete_validated_call() -> None:
    response = _Response()

    def observe(function: dict[str, str]) -> ModelDelta | None:
        return response.observe(
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call-1', 'function': function}]}}]}
        )

    started = observe({'name': 'run_python', 'arguments': '{"code":'})
    assert started is not None and started.activity and started.result is None
    assert observe({'name': 'run_python', 'arguments': ''}) is None
    argument = observe({'arguments': '"print(1)"}'})
    assert argument is not None and argument.activity and argument.result is None
    with pytest.raises(ProviderError):
        response.result(({'function': {'name': 'run_python'}},))
    response.observe({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})
    result = response.result(({'function': {'name': 'run_python'}},))
    assert result.finish_reason == 'stop' and len(result.result['tool_calls']) == 1


def test_a_deadline_reached_before_provider_start_does_not_claim_unknown_provider_usage() -> None:
    claim = ClaimedRun(uuid4(), 'chat', uuid4(), uuid4(), uuid4(), {})
    result = retrieval_execution_error(claim, ModelExecutionTimeout('total'), provider_started=False)
    assert result.code == 'execution_deadline_exceeded' and '主模型尚未开始' in result.message


def test_public_api_preserves_timeout_reasons_without_exposing_unrelated_internal_errors() -> None:
    value = RunSnapshot(
        id=uuid4(),
        resource_type='public',
        resource_id=uuid4(),
        status='interrupted',
        attempt_id=uuid4(),
        attempt_no=1,
        revision=2,
        projection_seq=1,
        text='部分内容',
        created_at=utcnow(),
        error=RunError(code='model_deadline_exceeded', message='已达到本轮生成时限'),
    )
    error = terminal_error(value)
    assert error.code == 'model_deadline_exceeded' and error.status_code == 504
    private = value.model_copy(update={'error': RunError(code='internal_failure', message='private-driver-details')})
    assert terminal_error(private).code == 'provider_unavailable'
    assert 'private-driver-details' not in terminal_error(private).message
