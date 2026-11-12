import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zhiheng.application.execution.executor import InferenceExecutor, InferenceProgress
from zhiheng.modules.models.contracts import ProviderError
from zhiheng.runtime.contracts import ClaimedRun


@pytest.mark.parametrize(('status', 'metric'), [(503, 'unknown'), (429, 'unknown'), (429, 'tpm')])
async def test_provider_failure_is_correlated_without_changing_settlement(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, status: int, metric: str
) -> None:
    rejected = status == 429
    claim = ClaimedRun(uuid4(), 'chat', uuid4(), uuid4(), uuid4(), {})
    error = ProviderError(
        definitive_rejection=rejected,
        reason='http_error',
        upstream_status=status,
        upstream_error_type='APIStatusError',
        rate_limit_metric=metric,
    )
    executor = object.__new__(InferenceExecutor)
    finish = AsyncMock()
    consume_count = 0

    async def consume(_claim: ClaimedRun, progress: InferenceProgress) -> None:
        nonlocal consume_count
        consume_count += 1
        progress.provider_started = True
        try:
            raise RuntimeError('private-url-header-body-input')
        except RuntimeError:
            raise error from None

    async def observe(_claim: ClaimedRun) -> str:
        await asyncio.Event().wait()
        return 'active'

    monkeypatch.setattr(executor, '_claim', AsyncMock(return_value=claim))
    monkeypatch.setattr(executor, '_consume', consume)
    monkeypatch.setattr(executor, '_observe', observe)
    monkeypatch.setattr(executor, '_finish', finish)
    await executor.execute(claim.id)

    assert consume_count == 1 and finish.await_count == 1
    args = finish.await_args.args
    assert args[0] == claim and args[1].provider_started and args[2] == 'failed'
    assert args[1].input_tokens is None and args[1].output_tokens is None
    assert args[3].code == ('provider_rejected' if rejected else 'provider_unavailable')
    assert args[3].message == '模型调用失败，请稍后重试。'
    assert finish.await_args.kwargs == {'rejected': rejected}
    records = [record for record in caplog.records if getattr(record, 'run_id', None) == str(claim.id)]
    assert len(records) == 1
    record = records[0]
    assert record.attempt_id == str(claim.attempt_id)
    assert record.provider_reason == 'http_error'
    assert record.upstream_status == status
    assert record.upstream_error_type == 'APIStatusError'
    assert record.rate_limit_metric == metric
    assert record.exc_info is None and record.stack_info is None
    assert 'private-url-header-body-input' not in caplog.text
    assert 'provider_reason=http_error' in caplog.text
    assert f'rate_limit_metric={metric}' in caplog.text


@pytest.mark.parametrize(
    ('resource_type', 'rerank'),
    [
        ('chat', False),
        ('connection_test', False),
        ('knowledge_embedding', False),
        ('knowledge_graph', False),
        ('knowledge_graph', True),
        ('knowledge_rerank', True),
    ],
)
@pytest.mark.parametrize('provider_started', [False, True])
async def test_timeout_is_transient_only_for_started_rerank(
    monkeypatch: pytest.MonkeyPatch, resource_type: str, rerank: bool, provider_started: bool
) -> None:
    from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest

    payload = {}
    if rerank:
        payload = ModelRequest(
            binding=ModelBinding(
                model_id=uuid4(),
                connection_id=uuid4(),
                upstream_model='mock',
                max_output_tokens=10,
                model_type='rerank',
            ),
            messages=(ModelMessage('user', 'query'),),
        ).model_dump(mode='json')
    claim = ClaimedRun(uuid4(), resource_type, uuid4(), uuid4(), uuid4(), payload)
    executor = object.__new__(InferenceExecutor)
    finish = AsyncMock()

    async def consume(_claim: ClaimedRun, progress: InferenceProgress) -> None:
        progress.provider_started = provider_started
        raise TimeoutError('private-provider-input')

    async def observe(_claim: ClaimedRun) -> str:
        await asyncio.Event().wait()
        return 'active'

    monkeypatch.setattr(executor, '_claim', AsyncMock(return_value=claim))
    monkeypatch.setattr(executor, '_consume', consume)
    monkeypatch.setattr(executor, '_observe', observe)
    monkeypatch.setattr(executor, '_finish', finish)
    await executor.execute(claim.id)

    assert finish.await_count == 1
    assert finish.await_args.args[2] == 'failed'
    error = finish.await_args.args[3]
    expected = (
        'knowledge_rerank_transient'
        if provider_started and rerank
        else ('inference_failed' if provider_started else 'execution_failed')
    )
    if provider_started and resource_type == 'knowledge_embedding':
        expected = 'embedding_timeout'
    assert error.code == expected
    assert 'private-provider-input' not in error.message


def test_upstream_balance_exhaustion_is_not_a_transient_rerank_failure() -> None:
    from zhiheng.application.execution.executor import retrieval_provider_error
    from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest

    payload = ModelRequest(
        binding=ModelBinding(
            model_id=uuid4(), connection_id=uuid4(), upstream_model='rerank', max_output_tokens=10, model_type='rerank'
        ),
        messages=(ModelMessage('user', 'query'),),
    ).model_dump(mode='json')
    claim = ClaimedRun(uuid4(), 'knowledge_rerank', uuid4(), uuid4(), uuid4(), payload)
    error = ProviderError(definitive_rejection=True, reason='insufficient_balance', upstream_status=429)
    result = retrieval_provider_error(claim, error)
    assert result.code == 'provider_rejected'
    assert result.message == '模型调用失败，请稍后重试。'
