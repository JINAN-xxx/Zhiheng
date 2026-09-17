import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import TypeAlias
from urllib.parse import quote

import httpx

from middlewares.trace_middleware.ctx import TraceCtx
from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_management_errors import (
    LiteLLMManagementAuthenticationError,
    LiteLLMManagementError,
    LiteLLMManagementMisconfiguredError,
    LiteLLMManagementTimeoutError,
    LiteLLMManagementUnavailableError,
    LiteLLMManagementUpstreamError,
    LiteLLMManagementUpstreamInvalidError,
)

ManagementFailure: TypeAlias = type[LiteLLMManagementError]


@dataclass(frozen=True)
class _RequestOutcome:
    failure: ManagementFailure | None
    payload: Mapping[str, object] | None = None


class LiteLLMManagementClient:
    """Platform 调用 LiteLLM 控制面的异步 HTTP 客户端。"""

    USER_AGENT = 'RuoYi-AI-Platform/0.1'

    def __init__(
        self,
        settings: LiteLLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            verify=settings.verify_ssl,
            transport=transport,
        )

    async def list_provider_fields(self) -> Mapping[str, object]:
        return await self._request_json('GET', '/public/providers/fields', authenticated=False)

    async def list_credentials(self) -> Mapping[str, object]:
        return await self._request_json('GET', '/credentials')

    async def get_credential(self, credential_name: str) -> Mapping[str, object]:
        path_name = _encode_path_segment(credential_name)
        operation = self._request_json('GET', f'/credentials/by_name/{path_name}')
        del credential_name, path_name
        return await operation

    async def create_credential(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        operation = self._request_json('POST', '/credentials', payload=payload)
        del payload
        return await operation

    async def rotate_credential(
        self,
        credential_name: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        path_name = _encode_path_segment(credential_name)
        operation = self._request_json('PATCH', f'/credentials/{path_name}', payload=payload)
        del credential_name, path_name, payload
        return await operation

    async def delete_credential(self, credential_name: str) -> Mapping[str, object]:
        path_name = _encode_path_segment(credential_name)
        operation = self._request_json('DELETE', f'/credentials/{path_name}')
        del credential_name, path_name
        return await operation

    async def list_deployments(self) -> Mapping[str, object]:
        return await self._request_json('GET', '/v2/model/info')

    async def model_deployment_exists(self, alias: str) -> bool:
        operation = self._request_json('GET', f'/v2/model/info?model={quote(alias, safe="")}')
        result = await operation
        exists = _model_deployment_exists(result, alias)
        del result, alias
        if exists is None:
            raise LiteLLMManagementUpstreamInvalidError
        return exists

    def _runtime_key_hash(self) -> str:
        if not self.settings.configured:
            raise LiteLLMManagementMisconfiguredError
        return sha256(self.settings.runtime_key.get_secret_value().strip().encode()).hexdigest()

    async def get_runtime_key_models(self) -> list[str]:
        """仅查询本平台调用密钥的模型授权，不在 URL 或返回值中暴露密钥。"""
        result = await self._request_json('POST', '/v2/key/info', payload={'keys': [self._runtime_key_hash()]})
        models = _runtime_key_models(result)
        del result
        if models is None:
            raise LiteLLMManagementUpstreamInvalidError
        return models

    async def update_runtime_key_models(self, models: list[str]) -> None:
        """只更新本平台调用密钥的模型列表，保留预算、角色和其他限制。"""
        if not models or any(not isinstance(model, str) or not model.strip() for model in models):
            raise ValueError('模型授权列表不能为空')
        operation = self._request_json(
            'POST',
            '/key/update',
            payload={'key': self._runtime_key_hash(), 'models': list(models)},
        )
        del models
        await operation

    async def create_deployment(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        operation = self._request_json('POST', '/model/new', payload=payload)
        del payload
        return await operation

    async def update_deployment(
        self,
        model_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        path_id = _encode_path_segment(model_id)
        operation = self._request_json('PATCH', f'/model/{path_id}/update', payload=payload)
        del model_id, path_id, payload
        return await operation

    async def delete_deployment(self, model_id: str) -> Mapping[str, object]:
        operation = self._request_json('POST', '/model/delete', payload={'id': model_id})
        del model_id
        return await operation

    async def test_connection(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        operation = self._request_json('POST', '/health/test_connection', payload=payload)
        del payload
        return await operation

    async def check_liveliness(self) -> Mapping[str, object]:
        return await self._request_json('GET', '/health/liveliness', authenticated=False, allow_string=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _build_headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {'User-Agent': self.USER_AGENT}
        request_id = TraceCtx.get_request_id()
        trace_id = TraceCtx.get_trace_id()
        if request_id:
            headers['request-id'] = request_id
        if trace_id:
            headers['trace-id'] = trace_id
        if authenticated:
            management_key = self.settings.management_key.get_secret_value().strip()
            if not management_key:
                raise LiteLLMManagementMisconfiguredError
            headers['Authorization'] = f'Bearer {management_key}'
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        authenticated: bool = True,
        allow_string: bool = False,
    ) -> Mapping[str, object]:
        operation = self._perform_json_request(
            method,
            path,
            payload=payload,
            authenticated=authenticated,
            allow_string=allow_string,
        )
        del method, path, payload
        outcome = await operation
        return await _unwrap_request_outcome(outcome)

    async def _perform_json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None,
        authenticated: bool,
        allow_string: bool,
    ) -> _RequestOutcome:
        try:
            async with self._client.stream(
                method,
                path,
                json=dict(payload) if payload is not None else None,
                headers=self._build_headers(authenticated=authenticated),
            ) as response:
                if not response.is_success:
                    failure = (
                        LiteLLMManagementAuthenticationError
                        if response.status_code in {401, 403}
                        else LiteLLMManagementUpstreamError
                    )
                    return _RequestOutcome(failure=failure)
                await response.aread()
                decoded = self._decode_json(response.content, allow_string=allow_string)
                if decoded is None:
                    return _RequestOutcome(failure=LiteLLMManagementUpstreamError)
                return _RequestOutcome(failure=None, payload=decoded)
        except httpx.TimeoutException:
            return _RequestOutcome(failure=LiteLLMManagementTimeoutError)
        except httpx.RequestError:
            return _RequestOutcome(failure=LiteLLMManagementUnavailableError)
        except LiteLLMManagementError as error:
            return _RequestOutcome(failure=type(error))

    @staticmethod
    def _decode_json(body: bytes, *, allow_string: bool) -> Mapping[str, object] | None:
        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if allow_string and isinstance(result, str):
            return {'status': result}
        if isinstance(result, list):
            return {'items': result}
        if isinstance(result, Mapping):
            return result
        return None


async def _raise_management_failure(failure: ManagementFailure) -> None:
    raise failure


def _runtime_key_models(result: Mapping[str, object]) -> list[str] | None:
    rows = result.get('info')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        return None
    models = rows[0].get('models')
    if not isinstance(models, list) or any(not isinstance(model, str) or not model.strip() for model in models):
        return None
    return list(models)


def _model_deployment_exists(result: Mapping[str, object], alias: str) -> bool | None:
    rows = result.get('data')
    if not isinstance(rows, list):
        return None
    return any(isinstance(row, Mapping) and row.get('model_name') == alias for row in rows)


def _encode_path_segment(identifier: str) -> str:
    return quote(identifier, safe='').replace('.', '%2E')


async def _unwrap_request_outcome(outcome: _RequestOutcome) -> Mapping[str, object]:
    if outcome.failure is not None:
        await _raise_management_failure(outcome.failure)
    if outcome.payload is None:
        raise AssertionError('不可达')
    return outcome.payload
