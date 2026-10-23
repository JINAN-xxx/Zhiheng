from collections.abc import Callable
from time import perf_counter
from typing import Protocol

from plugins.ai.domain.model_runtime import (
    ModelChatRequest,
    ModelEmbeddingRequest,
    ModelMessage,
    ModelRerankDocument,
    ModelRerankRequest,
    ModelRuntimeEngine,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.vo.ai_developer_api_vo import ModelSmokeTestResultModel
from plugins.ai.service.model_service import ModelService, ResolvedModel


class SmokeTestCaller(Protocol):
    async def resolve_model(self, model_id: str, expected_type: str | None = None) -> ResolvedModel: ...


class ModelSmokeTestService:
    """模型中心类型化连接测试：按模型类型执行最小 Runtime 调用。"""

    def __init__(
        self,
        model_service: SmokeTestCaller | ModelService,
        runtime: ModelRuntimeEngine,
        *,
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._model_service = model_service
        self._runtime = runtime
        self._timer = timer

    async def run(self, model_id: str) -> ModelSmokeTestResultModel:
        started_at = self._timer()
        model_type = 'chat'
        try:
            resolved = await self._model_service.resolve_model(model_id)
            model_type = resolved.model_type
            await self._call_by_type(resolved)
        except ModelServiceError as error:
            return ModelSmokeTestResultModel(
                model_type=model_type,
                success=False,
                message=error.public_message,
            )
        except Exception:
            return ModelSmokeTestResultModel(
                model_type=model_type,
                success=False,
                message='连接测试失败',
            )
        latency_ms = max(int((self._timer() - started_at) * 1000), 0)
        return ModelSmokeTestResultModel(
            model_type=model_type,
            success=True,
            latency_ms=latency_ms,
            message='连接测试成功',
        )

    async def _call_by_type(self, resolved: ResolvedModel) -> None:
        model_type = resolved.model_type
        if model_type == 'chat':
            await self._runtime.chat(
                ModelChatRequest(
                    engine_model=resolved.engine_model_snapshot,
                    messages=(ModelMessage(role='user', content='连接测试'),),
                )
            )
            return
        if model_type == 'embedding':
            await self._runtime.embed(
                ModelEmbeddingRequest(engine_model=resolved.engine_model_snapshot, inputs=('连接测试',))
            )
            return
        if model_type == 'rerank':
            await self._runtime.rerank(
                ModelRerankRequest(
                    engine_model=resolved.engine_model_snapshot,
                    query='连接测试',
                    documents=(ModelRerankDocument(text='连接测试文档'),),
                )
            )
            return
        raise ModelServiceError.not_found()
