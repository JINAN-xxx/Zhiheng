import asyncio
from dataclasses import dataclass

import pytest

from plugins.ai.infrastructure.chat_knowledge_adapter import ChatKnowledgeAdapter
from plugins.ai.service.platform_chat_service import PlatformChatError


@dataclass(frozen=True)
class Hit:
    content: str = 'Reference text'
    source_id: str = 'source-1'
    title: str = 'Guide'
    knowledge_base_id: str = 'kb-1'


class FakeKnowledgeRuntime:
    def __init__(self, hits: object = None, error: BaseException | None = None) -> None:
        self.hits = [Hit()] if hits is None else hits
        self.error = error
        self.calls = []

    async def retrieve_context(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.hits


@pytest.mark.asyncio
async def test_knowledge_adapter_passes_explicit_identity_bindings_and_query() -> None:
    runtime = FakeKnowledgeRuntime()
    passages = await ChatKnowledgeAdapter(runtime).resolve(7, ['kb-1'], 'Question')
    assert runtime.calls == [
        {
            'owner_user_id': 7,
            'knowledge_base_ids': ['kb-1'],
            'query': 'Question',
            'top_k': 8,
        }
    ]
    assert passages[0].content == 'Reference text'
    assert passages[0].knowledge_base_id == 'kb-1'


@pytest.mark.asyncio
async def test_no_selection_does_not_call_knowledge_runtime() -> None:
    runtime = FakeKnowledgeRuntime(error=AssertionError('must not retrieve'))
    assert await ChatKnowledgeAdapter(runtime).resolve(7, [], 'Question') == ()
    assert runtime.calls == []
    assert await ChatKnowledgeAdapter(None).resolve(7, [], 'Question') == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'hits',
    [
        [Hit(knowledge_base_id='other-owner-kb')],
        [Hit()] * 9,
        [Hit(content='中' * 8001)],
        [Hit(content=' ')],
        [Hit(source_id='')],
        [Hit(title=3)],
        {'content': 'not a sequence'},
    ],
)
async def test_invalid_retrieval_data_fails_closed(hits: object) -> None:
    with pytest.raises(PlatformChatError) as raised:
        await ChatKnowledgeAdapter(FakeKnowledgeRuntime(hits)).resolve(7, ['kb-1'], 'Question')
    assert raised.value.code == 'CHAT_KNOWLEDGE_UNAVAILABLE'


@pytest.mark.asyncio
async def test_missing_or_failing_runtime_returns_fixed_error_without_secrets() -> None:
    for runtime in (None, FakeKnowledgeRuntime(error=ValueError('private-document secret-token'))):
        with pytest.raises(PlatformChatError) as raised:
            await ChatKnowledgeAdapter(runtime).resolve(7, ['kb-1'], 'Question')
        assert raised.value.code == 'CHAT_KNOWLEDGE_UNAVAILABLE'
        assert 'secret-token' not in str(raised.value.public_dict())


@pytest.mark.asyncio
async def test_retrieval_timeout_and_cancellation() -> None:
    class WaitingRuntime:
        async def retrieve_context(self, **kwargs: object) -> object:
            await asyncio.Event().wait()

    with pytest.raises(PlatformChatError) as raised:
        await ChatKnowledgeAdapter(WaitingRuntime(), timeout_seconds=0.001).resolve(7, ['kb-1'], 'Question')
    assert raised.value.code == 'CHAT_KNOWLEDGE_UNAVAILABLE'
    with pytest.raises(asyncio.CancelledError):
        await ChatKnowledgeAdapter(FakeKnowledgeRuntime(error=asyncio.CancelledError())).resolve(
            7, ['kb-1'], 'Question'
        )
