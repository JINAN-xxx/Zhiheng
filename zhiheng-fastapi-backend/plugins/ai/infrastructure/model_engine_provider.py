from fastapi import Request

from plugins.ai.hooks import MANAGEMENT_CLIENT_STATE_NAME, RUNTIME_CLIENT_STATE_NAME
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.service.model_engine_service import (
    LiteLLMDeploymentReferenceQuery,
    ModelEngineService,
)


def get_model_engine_service(request: Request) -> ModelEngineService:
    """解析当前 Worker 的共享客户端，但不转移其生命周期所有权。"""
    management_client = _management_client(request)
    adapter = LiteLLMManagementAdapter()
    return ModelEngineService(
        management_client,
        adapter,
        LiteLLMDeploymentReferenceQuery(management_client, adapter),
        runtime_client=_runtime_client(request),
    )


def _management_client(request: Request) -> LiteLLMManagementClient | None:
    client = getattr(request.app.state, MANAGEMENT_CLIENT_STATE_NAME, None)
    return client if isinstance(client, LiteLLMManagementClient) else None


def _runtime_client(request: Request) -> LiteLLMRuntimeClient | None:
    client = getattr(request.app.state, RUNTIME_CLIENT_STATE_NAME, None)
    return client if isinstance(client, LiteLLMRuntimeClient) else None
