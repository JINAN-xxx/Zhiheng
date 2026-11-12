from uuid import uuid4

from zhiheng.application.execution.executor import retrieval_execution_error
from zhiheng.runtime.contracts import ClaimedRun


def test_index_timeout_is_actionable_without_changing_chat_diagnostics() -> None:
    claim = ClaimedRun(uuid4(), 'knowledge_embedding', uuid4(), uuid4(), uuid4(), {})
    error = retrieval_execution_error(claim, TimeoutError(), provider_started=True)
    assert error.code == 'embedding_timeout' and '超时' in error.message and '用量' in error.message
    chat = ClaimedRun(uuid4(), 'chat', uuid4(), uuid4(), uuid4(), {})
    assert retrieval_execution_error(chat, TimeoutError(), provider_started=True).code == 'inference_failed'


def test_embedding_preparation_timeout_does_not_claim_provider_was_called() -> None:
    claim = ClaimedRun(uuid4(), 'knowledge_embedding', uuid4(), uuid4(), uuid4(), {})
    error = retrieval_execution_error(claim, TimeoutError(), provider_started=False)
    assert error.code == 'execution_failed'
