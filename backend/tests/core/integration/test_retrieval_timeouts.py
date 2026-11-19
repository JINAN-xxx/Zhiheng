from datetime import timedelta
from time import sleep
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_knowledge_required_rerank import finish_query
from tests.core.integration.test_knowledge_retrieval_v2 import pending_ranker, ready
from tests.core.provider import ProviderHandler, ProviderServer
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.knowledge.orm import RetrievalIntent
from zhiheng.modules.models.public import ModelRequest
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    return Settings.model_validate(
        {
            **settings.model_dump(),
            'provider_first_response_timeout_seconds': 8,
            'provider_idle_timeout_seconds': 2,
            'provider_total_timeout_seconds': 10,
            'knowledge_query_timeout_seconds': 20,
            'knowledge_rerank_timeout_seconds': 7,
            **getattr(request, 'param', {}),
        }
    )


async def test_a_valid_rerank_can_finish_beyond_the_old_five_second_limit_without_repeating(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, *_ = await ready(admin, app, provider)
    container = app.state.container
    intent, run_id = await pending_ranker(admin, app, base)
    async with container.database.sessions() as session:
        model = await session.get(Run, run_id)
        retrieval = await session.get(RetrievalIntent, UUID(intent['id']))
        request = ModelRequest.model_validate(model.payload)
        assert request.execution_timeout_seconds == 7
        assert request.execution_deadline <= retrieval.deadline
        assert retrieval.deadline - retrieval.created_at > timedelta(seconds=19)
        assert retrieval.dependencies['rerank_timeout_seconds'] == 7
    container.knowledge_preparation.rerank_timeout_seconds = 0.1
    original = ProviderHandler.structured

    def delayed(handler: ProviderHandler, body: dict[str, object]) -> None:
        if handler.path.endswith('/rerank'):
            sleep(5.2)
        original(handler, body)

    monkeypatch.setattr(ProviderHandler, 'structured', delayed)
    await execute_run(container, run_id)
    result = await finish_query(admin, app, intent)
    assert result['status'] == 'completed' and result['hits'], result
    await execute_run(container, run_id)
    assert len([entry for entry in provider.requests if 'documents' in entry]) == 1


@pytest.mark.parametrize('settings', [{'knowledge_query_timeout_seconds': 3}], indirect=True)
async def test_rerank_budget_is_capped_by_the_frozen_parent_retrieval_deadline(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base, *_ = await ready(admin, app, provider)
    intent, run_id = await pending_ranker(admin, app, base)
    async with app.state.container.database.sessions() as session:
        model = await session.get(Run, run_id)
        retrieval = await session.get(RetrievalIntent, UUID(intent['id']))
        request = ModelRequest.model_validate(model.payload)
        assert request.execution_deadline <= retrieval.deadline
        assert 0 < request.execution_timeout_seconds < 3
    result = await admin.post(f'/api/knowledge/preparations/{intent["id"]}/cancel', json={})
    assert result.status_code == 200, result.text
