from uuid import uuid4

import pytest

from zhiheng.modules.agents.public import KnowledgeContext, knowledge_context
from zhiheng.shared.errors import AppError


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['exception', 'wrong-source', 'invalid', 'too-long'])
async def test_knowledge_provider_failures_are_stable(failure: str) -> None:
    kb = uuid4()

    class Provider:
        async def retrieve(self, **kwargs: object) -> tuple[KnowledgeContext, ...]:
            if failure == 'exception':
                raise RuntimeError('private connection detail')
            if failure == 'invalid':
                return (KnowledgeContext(kb, uuid4(), ''),)
            if failure == 'wrong-source':
                return (KnowledgeContext(uuid4(), uuid4(), 'other'),)
            return (KnowledgeContext(kb, uuid4(), 'x' * 24576),)

    with pytest.raises(AppError) as caught:
        await knowledge_context(
            Provider(), owner_id=uuid4(), agent_id=uuid4(), application_id=None, knowledge_base_ids=(kb,), query='q'
        )
    assert caught.value.code == 'knowledge_unavailable' and caught.value.status_code == 503
