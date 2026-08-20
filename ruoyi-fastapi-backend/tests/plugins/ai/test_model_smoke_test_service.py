import pytest

from plugins.ai.domain.model_runtime import (
    ModelChatRequest,
    ModelChatResponse,
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelEmbeddingVector,
    ModelRerankRequest,
    ModelRerankResponse,
    ModelRerankResult,
    ModelUsage,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.service.model_service import ResolvedModel
from plugins.ai.service.model_smoke_test_service import ModelSmokeTestService

MODEL_ID = 'mdl_00000000000040008000000000000001'
BINDING_ID = 'bnd_00000000000040008000000000000001'


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse:
        self.calls.append('chat')
        if self.error is not None:
            raise self.error
        return ModelChatResponse(text='ok', usage=ModelUsage(input_tokens=1, output_tokens=1))

    async def embed(self, request: ModelEmbeddingRequest) -> ModelEmbeddingResponse:
        self.calls.append('embed')
        if self.error is not None:
            raise self.error
        return ModelEmbeddingResponse(vectors=(ModelEmbeddingVector(index=0, values=(0.1,)),), input_tokens=1)

    async def rerank(self, request: ModelRerankRequest) -> ModelRerankResponse:
        self.calls.append('rerank')
        if self.error is not None:
            raise self.error
        return ModelRerankResponse(
            request_id='rrk_00000000000040008000000000000001',
            results=(ModelRerankResult(index=0, relevance_score=1.0),),
            input_tokens=1,
            search_units=1,
        )

    async def list_model_ids(self) -> set[str]:
        return set()

    def stream_chat(self, request: ModelChatRequest) -> object:
        raise AssertionError('unused')

    async def aclose(self) -> None:
        return None


class FakeModelService:
    def __init__(self, model_type: str) -> None:
        self.model_type = model_type
        self.error: BaseException | None = None

    async def resolve_model(self, model_id: str, expected_type: str | None = None) -> ResolvedModel:
        del expected_type
        if self.error is not None:
            raise self.error
        return ResolvedModel(
            platform_model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='runtime-model',
            model_type=self.model_type,
        )


@pytest.mark.parametrize('model_type', ('chat', 'embedding', 'rerank'))
@pytest.mark.asyncio
async def test_smoke_test_runs_typed_runtime_call(model_type: str) -> None:
    runtime = FakeRuntime()
    service = ModelSmokeTestService(FakeModelService(model_type), runtime)

    result = await service.run(MODEL_ID)

    assert result.success is True
    assert result.model_type == model_type
    assert result.latency_ms >= 0
    assert runtime.calls == [{'chat': 'chat', 'embedding': 'embed', 'rerank': 'rerank'}[model_type]]


@pytest.mark.asyncio
async def test_smoke_test_returns_stable_failure_on_runtime_error() -> None:
    runtime = FakeRuntime()
    runtime.error = ModelServiceError.runtime_unavailable()
    service = ModelSmokeTestService(FakeModelService('embedding'), runtime)

    result = await service.run(MODEL_ID)

    assert result.success is False
    assert result.model_type == 'embedding'
    assert result.message != ''


@pytest.mark.asyncio
async def test_smoke_test_reports_missing_model_without_runtime_call() -> None:
    runtime = FakeRuntime()
    model_service = FakeModelService('chat')
    model_service.error = ModelServiceError.not_found()
    service = ModelSmokeTestService(model_service, runtime)

    result = await service.run(MODEL_ID)

    assert result.success is False
    assert result.model_type == 'chat'
    assert runtime.calls == []
