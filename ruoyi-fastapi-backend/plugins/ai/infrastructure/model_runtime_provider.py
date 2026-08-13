from collections.abc import AsyncIterator

from fastapi import Request

from plugins.ai.domain.model_runtime import ModelChatRequest, ModelChatResponse, ModelRuntimeEngine, ModelStreamEvent
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.hooks import CLIENT_STATE_NAME
from plugins.ai.infrastructure.litellm_runtime_adapter import LiteLLMRuntimeAdapter


class MissingModelRuntimeEngine:
    """允许本地主数据读取，并在实际访问 Runtime 时返回稳定配置错误。"""

    async def list_model_ids(self) -> set[str]:
        raise ModelServiceError.runtime_misconfigured()

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse:
        del request
        raise ModelServiceError.runtime_misconfigured()

    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        raise ModelServiceError.runtime_misconfigured()

    async def aclose(self) -> None:
        return None


class _SharedModelRuntimeEngine:
    """委托共享 Runtime，但不接管应用级客户端的生命周期。"""

    def __init__(self, engine: ModelRuntimeEngine) -> None:
        self._engine = engine

    async def list_model_ids(self) -> set[str]:
        return await self._engine.list_model_ids()

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse:
        return await self._engine.chat(request)

    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]:
        return self._engine.stream_chat(request)

    async def aclose(self) -> None:
        return None


def get_model_runtime_engine(request: Request) -> ModelRuntimeEngine:
    """解析当前 Worker 共享的模型运行时引擎，不转移客户端所有权。"""
    client = getattr(request.app.state, CLIENT_STATE_NAME, None)
    if client is None:
        return MissingModelRuntimeEngine()
    return _SharedModelRuntimeEngine(LiteLLMRuntimeAdapter(client))
