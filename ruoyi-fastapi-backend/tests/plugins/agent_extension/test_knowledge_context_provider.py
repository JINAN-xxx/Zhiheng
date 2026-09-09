from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.agent_extension.domain.agent_knowledge import (
    KnowledgeIdentity,
    KnowledgeRetrievalError,
    resolve_agent_knowledge,
)
from plugins.agent_extension.infrastructure.knowledge_context_provider import PlatformKnowledgeProvider


@pytest.mark.asyncio
async def test_provider_preserves_authenticated_owner_binding_and_real_source() -> None:
    runtime = SimpleNamespace(retrieve_context=AsyncMock(return_value=[
        SimpleNamespace(content='保留发票。', source_id='file-a', title='报销规定', knowledge_base_id='kb-a'),
    ]))
    app = SimpleNamespace(state=SimpleNamespace(knowledge_runtime=runtime))
    provider = PlatformKnowledgeProvider(app)
    passages = await resolve_agent_knowledge(
        provider, agent_id='agent-a', owner_user_id=7, application_id='app-a',
        knowledge_base_ids=['kb-a'], query='需要什么材料？',
    )
    runtime.retrieve_context.assert_awaited_once_with(
        owner_user_id=7, knowledge_base_ids=('kb-a',), query='需要什么材料？', top_k=8,
    )
    assert passages[0].source_id == 'file-a'
    assert passages[0].knowledge_base_id == 'kb-a'
    assert passages[0].content == '保留发票。'


@pytest.mark.asyncio
async def test_provider_observes_runtime_removal_instead_of_using_stale_reference() -> None:
    runtime = SimpleNamespace(retrieve_context=AsyncMock(return_value=[]))
    app = SimpleNamespace(state=SimpleNamespace(knowledge_runtime=runtime))
    provider = PlatformKnowledgeProvider(app)
    identity = KnowledgeIdentity('agent-a', 7, None)
    assert await provider.retrieve(identity=identity, knowledge_base_ids=('kb-a',), query='hello') == ()
    del app.state.knowledge_runtime
    with pytest.raises(RuntimeError, match='unavailable'):
        await provider.retrieve(identity=identity, knowledge_base_ids=('kb-a',), query='hello')


@pytest.mark.asyncio
async def test_provider_boundary_rejects_misattributed_sources() -> None:
    runtime = SimpleNamespace(retrieve_context=AsyncMock(return_value=[
        SimpleNamespace(content='Not authorized', source_id='file-b', title='Private', knowledge_base_id='kb-b'),
    ]))
    provider = PlatformKnowledgeProvider(SimpleNamespace(state=SimpleNamespace(knowledge_runtime=runtime)))
    with pytest.raises(KnowledgeRetrievalError):
        await resolve_agent_knowledge(
            provider, agent_id='agent-a', owner_user_id=7, application_id=None,
            knowledge_base_ids=['kb-a'], query='hello',
        )
