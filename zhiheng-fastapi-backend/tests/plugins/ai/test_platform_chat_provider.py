import pytest
from fastapi import FastAPI
from pytest import MonkeyPatch
from starlette.requests import Request

from plugins.ai.infrastructure import platform_chat_provider
from plugins.ai.infrastructure.chat_file_adapter import ChatFileAdapter


class TransactionalRequestSession:
    def in_transaction(self) -> bool:
        return True


def test_chat_provider_does_not_reuse_request_transaction_for_model_resolution(monkeypatch: MonkeyPatch) -> None:
    app = FastAPI()
    request = Request({'type': 'http', 'method': 'POST', 'path': '/ai/chat/send', 'headers': [], 'app': app})

    class FakeRuntimeClient:
        pass

    app.state.litellm_runtime_client = FakeRuntimeClient()
    monkeypatch.setattr(platform_chat_provider, 'get_model_runtime_engine', lambda request: object())

    platform_chat_provider.get_platform_chat_service(request, TransactionalRequestSession())


@pytest.mark.asyncio
async def test_chat_provider_uses_app_state_knowledge_runtime(monkeypatch: MonkeyPatch) -> None:
    app = FastAPI()
    calls = []

    class FakeKnowledgeRuntime:
        async def retrieve_context(self, **kwargs: object) -> list[object]:
            calls.append(kwargs)
            return []

    app.state.knowledge_runtime = FakeKnowledgeRuntime()
    request = Request({'type': 'http', 'method': 'POST', 'path': '/ai/chat/send', 'headers': [], 'app': app})
    monkeypatch.setattr(platform_chat_provider, 'get_model_runtime_engine', lambda request: object())
    service = platform_chat_provider.get_platform_chat_service(request, TransactionalRequestSession())
    assert await service._knowledge_resolver.resolve(17, ['kb-1'], 'Question') == ()
    assert calls == [
        {
            'owner_user_id': 17,
            'knowledge_base_ids': ['kb-1'],
            'query': 'Question',
            'top_k': 8,
        }
    ]


def test_chat_provider_injects_shared_managed_file_adapter(monkeypatch: MonkeyPatch) -> None:
    app = FastAPI()
    request = Request({'type': 'http', 'method': 'POST', 'path': '/ai/chat/send', 'headers': [], 'app': app})
    monkeypatch.setattr(platform_chat_provider, 'get_model_runtime_engine', lambda request: object())
    service = platform_chat_provider.get_platform_chat_service(request, TransactionalRequestSession())
    assert isinstance(service._image_resolver, ChatFileAdapter)
    assert service._attachment_resolver is service._image_resolver
