"""Bounded, credential-isolated provider metadata validation before saving a model."""

import asyncio
import json
from http import HTTPStatus
from urllib.parse import quote

import httpx

from zhiheng.shared.errors import AppError

MAX_CATALOG_BYTES = 2 * 1024 * 1024
CHECK_TIMEOUT_SECONDS = 10


async def _read(client: httpx.AsyncClient, url: str) -> tuple[int, object]:
    async with client.stream('GET', url) as response:
        if response.status_code != HTTPStatus.OK:
            return response.status_code, None
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_CATALOG_BYTES:
                raise AppError('model_validation_response', '服务商模型目录超过校验大小限制', 502)
        try:
            return response.status_code, json.loads(content)
        except (ValueError, RecursionError) as error:
            raise AppError('model_validation_response', '服务商未返回有效模型目录，请检查 API 地址', 502) from error


def _status(status: int) -> None:
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        raise AppError('model_validation_auth', '服务商密钥无效或没有访问权限，请检查 API Key', 422)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        raise AppError('model_validation_rate_limited', '服务商校验请求被限流，请稍后重试', 429)
    if status != HTTPStatus.OK:
        raise AppError('model_validation_unavailable', '无法读取服务商模型信息，请检查 API 地址和服务状态', 502)


async def validate_catalog(*, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
    headers = {
        'Authorization': 'Bearer ' + api_key,
        'Accept': 'application/json',
        'User-Agent': 'zhiheng-platform/0.1.0',
    }

    async def check() -> None:
        async with httpx.AsyncClient(
            headers=headers, trust_env=False, follow_redirects=False, timeout=timeout_seconds
        ) as client:
            root = base_url.rstrip('/')
            status, body = await _read(client, root + '/models')
            if status in {HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED}:
                status, body = await _read(client, root + '/models/' + quote(model, safe=''))
                _status(status)
                available = isinstance(body, dict) and body.get('id') == model
            else:
                _status(status)
                if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                    raise AppError('model_validation_response', '服务商模型目录格式无效，请检查 API 地址', 502)
                available = any(isinstance(row, dict) and row.get('id') == model for row in body['data'])
            if not available:
                raise AppError(
                    'model_validation_not_found', '服务商未提供该模型或当前密钥无权使用，请核对上游模型标识', 422
                )

    try:
        await asyncio.wait_for(check(), timeout=min(CHECK_TIMEOUT_SECONDS, timeout_seconds))
    except (asyncio.TimeoutError, httpx.TimeoutException) as error:
        raise AppError('model_validation_timeout', '服务商连接校验超时，请检查 API 地址后重试', 504) from error
    except httpx.HTTPError as error:
        raise AppError('model_validation_connection', '无法连接服务商，请检查 API 地址和网络', 502) from error
