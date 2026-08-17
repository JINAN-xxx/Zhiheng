from fastapi import FastAPI
from pytest import MonkeyPatch
from starlette.requests import Request

from plugins.ai.infrastructure import platform_chat_provider


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
