from collections.abc import Mapping
from datetime import datetime, timezone
from time import perf_counter

from fastapi import HTTPException, Request, Response

from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import PreAuthDependency
from common.router import APIRouterPro
from common.vo import DataResponseModel
from plugins.ai.entity.vo.ai_runtime_vo import AiRuntimeHealthModel, AiRuntimeModelInfo
from plugins.ai.hooks import CLIENT_STATE_NAME
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_errors import LiteLLMError, LiteLLMMisconfiguredError, LiteLLMUpstreamError
from utils.response_util import ResponseUtil

ai_runtime_controller = APIRouterPro(
    prefix='/ai/runtime',
    order_num=19,
    tags=['AI管理-Runtime诊断'],
    dependencies=[PreAuthDependency()],
)


def _get_runtime_client(request: Request) -> LiteLLMRuntimeClient | None:
    return getattr(request.app.state, CLIENT_STATE_NAME, None)


def _latency_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)


def _public_error_detail(error: LiteLLMError) -> dict[str, str]:
    return {'code': error.code, 'message': error.default_message}


def _normalize_models(response: Mapping[str, object]) -> list[AiRuntimeModelInfo]:
    raw_models = response.get('data', [])
    if not isinstance(raw_models, list):
        raise LiteLLMUpstreamError

    result = []
    for raw_model in raw_models:
        if not isinstance(raw_model, Mapping):
            raise LiteLLMUpstreamError
        model_id = raw_model.get('id')
        if not isinstance(model_id, str) or not model_id:
            raise LiteLLMUpstreamError
        object_name = raw_model.get('object', 'model')
        owned_by = raw_model.get('owned_by')
        result.append(
            AiRuntimeModelInfo(
                id=model_id,
                object=object_name if isinstance(object_name, str) else 'model',
                owned_by=owned_by if isinstance(owned_by, str) else None,
            )
        )
    return result


@ai_runtime_controller.get(
    '/health',
    summary='获取 LiteLLM Runtime 健康状态',
    response_model=DataResponseModel[AiRuntimeHealthModel],
    dependencies=[UserInterfaceAuthDependency('ai:runtime:query')],
)
async def get_ai_runtime_health(request: Request) -> Response:
    """返回配置状态和当前 LiteLLM 可达性。"""
    started_at = perf_counter()
    client = _get_runtime_client(request)
    if client is None or not client.settings.configured:
        status_model = AiRuntimeHealthModel(
            status='misconfigured',
            configured=False,
            latency_ms=_latency_ms(started_at),
            checked_at=datetime.now(timezone.utc),
            message='LiteLLM Runtime 尚未完成配置',
        )
        return ResponseUtil.success(data=status_model)

    try:
        await client.check_liveliness()
    except LiteLLMError:
        status_model = AiRuntimeHealthModel(
            status='unavailable',
            configured=True,
            latency_ms=_latency_ms(started_at),
            checked_at=datetime.now(timezone.utc),
            message='LiteLLM Runtime 当前不可用',
        )
        return ResponseUtil.success(data=status_model)

    status_model = AiRuntimeHealthModel(
        status='healthy',
        configured=True,
        latency_ms=_latency_ms(started_at),
        checked_at=datetime.now(timezone.utc),
        message='LiteLLM Runtime 运行正常',
    )
    return ResponseUtil.success(data=status_model)


@ai_runtime_controller.get(
    '/models',
    summary='获取 LiteLLM Runtime 模型列表',
    response_model=DataResponseModel[list[AiRuntimeModelInfo]],
    dependencies=[UserInterfaceAuthDependency('ai:runtime:query')],
)
async def get_ai_runtime_models(request: Request) -> Response:
    """返回当前 Runtime Key 可访问的规范化模型列表。"""
    client = _get_runtime_client(request)
    if client is None:
        error = LiteLLMMisconfiguredError()
        raise HTTPException(status_code=error.status_code, detail=_public_error_detail(error))

    try:
        models = _normalize_models(await client.list_models())
    except LiteLLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=_public_error_detail(exc)) from exc
    return ResponseUtil.success(data=models)
