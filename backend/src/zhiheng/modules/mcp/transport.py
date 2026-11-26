"""Bounded Streamable HTTP client for MCP tool discovery and calls."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from zhiheng.modules.mcp.contracts import ExternalToolResult, ToolDefinition
from zhiheng.shared.errors import AppError

PROTOCOLS = ('2025-06-18', '2025-03-26')
MAX_REPLY_BYTES = 256 * 1024
MAX_TOOLS = 100
MAX_DISCOVERY_PAGES = 10
MAX_RESULT_CHARS = 8000
MAX_SESSION_ID = 256
MAX_CURSOR = 1024
T = TypeVar('T')


def canonical_endpoint(value: str) -> str:
    try:
        url = urlsplit(value)
        if (
            url.scheme not in {'http', 'https'}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or '?' in value
            or '#' in value
            or '\\' in value
            or any(char.isspace() or not char.isprintable() for char in value)
        ):
            raise ValueError
        return str(httpx.URL(value))
    except (ValueError, httpx.InvalidURL):
        raise AppError('mcp_endpoint_invalid', '请输入合法的 HTTP(S) 地址，认证信息请单独填写', 422) from None


def protocol_error() -> AppError:
    return AppError('mcp_protocol_error', 'MCP 服务返回了无效或不支持的协议消息', 502)


class _Session:
    def __init__(self, client: httpx.AsyncClient, endpoint: str) -> None:
        self.client = client
        self.endpoint = endpoint
        self.headers: dict[str, str] = {}
        self.sequence = 0

    async def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.sequence += 1
        body = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params}
        async with self.client.stream('POST', self.endpoint, headers=self.headers, json=body) as response:
            response.raise_for_status()
            if method == 'initialize' and (session_id := response.headers.get('mcp-session-id')):
                if (
                    len(session_id) > MAX_SESSION_ID
                    or not session_id.isascii()
                    or not session_id.isprintable()
                    or ' ' in session_id
                ):
                    raise protocol_error()
                self.headers['mcp-session-id'] = session_id
            result = await _read_result(response, self.sequence)
        return result

    async def initialize(self) -> None:
        result = await self.request(
            'initialize',
            {'protocolVersion': PROTOCOLS[0], 'capabilities': {}, 'clientInfo': {'name': 'DAIP', 'version': '1.0'}},
        )
        version = result.get('protocolVersion')
        capabilities = result.get('capabilities')
        if not isinstance(version, str) or version not in PROTOCOLS or not isinstance(capabilities, dict):
            raise protocol_error()
        if 'tools' not in capabilities:
            raise AppError('mcp_tools_unsupported', '此 MCP 服务未提供工具', 422)
        self.headers['mcp-protocol-version'] = version
        async with self.client.stream(
            'POST',
            self.endpoint,
            headers=self.headers,
            json={'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        ) as response:
            response.raise_for_status()

    async def close(self) -> None:
        if 'mcp-session-id' in self.headers:
            with suppress(httpx.HTTPError, TimeoutError):
                await asyncio.wait_for(self._delete(), 2)

    async def _delete(self) -> None:
        async with self.client.stream('DELETE', self.endpoint, headers=self.headers):
            pass


def _decode(data: bytes, request_id: int) -> dict[str, object] | None:
    try:
        value = json.loads(data)
    except (UnicodeError, ValueError, RecursionError):
        raise protocol_error() from None
    if not isinstance(value, dict) or value.get('jsonrpc') != '2.0':
        raise protocol_error()
    if value.get('id') != request_id:
        return None
    if 'error' in value:
        raise AppError('mcp_remote_error', 'MCP 服务拒绝了本次请求，请检查工具配置与参数', 502)
    result = value.get('result')
    if not isinstance(result, dict):
        raise protocol_error()
    return result


async def _read_result(response: httpx.Response, request_id: int) -> dict[str, object]:
    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
        raise protocol_error()
    media = response.headers.get('content-type', '').partition(';')[0].strip().lower()
    if media not in {'application/json', 'text/event-stream'}:
        raise protocol_error()
    buffered = bytearray()
    event_data: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes(chunk_size=8192 if media == 'application/json' else None):
        received += len(chunk)
        if received > MAX_REPLY_BYTES:
            raise AppError('mcp_response_too_large', 'MCP 服务响应超过 256 KB', 502)
        buffered.extend(chunk)
        if media == 'text/event-stream':
            while (line_end := buffered.find(b'\n')) >= 0:
                line = bytes(buffered[:line_end]).removesuffix(b'\r')
                del buffered[: line_end + 1]
                if line.startswith(b'data:'):
                    event_data.append(line[5:].removeprefix(b' '))
                elif not line and event_data:
                    result = _decode(b'\n'.join(event_data), request_id)
                    event_data.clear()
                    if result is not None:
                        return result
    if media == 'application/json' and (result := _decode(bytes(buffered), request_id)) is not None:
        return result
    raise protocol_error()


class McpTransport:
    def __init__(self, allowed_endpoints: tuple[str, ...]) -> None:
        self.allowed_endpoints = frozenset(canonical_endpoint(value) for value in allowed_endpoints)

    def allowed(self, endpoint: str) -> str:
        endpoint = canonical_endpoint(endpoint)
        if endpoint not in self.allowed_endpoints:
            raise AppError('mcp_endpoint_not_allowed', '此服务地址尚未加入部署允许清单', 403)
        return endpoint

    @asynccontextmanager
    async def session(self, endpoint: str, token: str | None) -> AsyncIterator[_Session]:
        headers = {'accept': 'application/json, text/event-stream', 'accept-encoding': 'identity'}
        if token:
            headers['authorization'] = f'Bearer {token}'
        async with httpx.AsyncClient(headers=headers, timeout=10, follow_redirects=False, trust_env=False) as client:
            connection = _Session(client, self.allowed(endpoint))
            try:
                await connection.initialize()
                yield connection
            except (httpx.HTTPError, ValueError) as error:
                raise AppError('mcp_unreachable', 'MCP 服务连接失败，请检查地址和凭据', 502) from error
            finally:
                await connection.close()

    async def discover(self, endpoint: str, token: str | None) -> list[ToolDefinition]:
        return await self._bounded(self._discover(endpoint, token))

    async def _discover(self, endpoint: str, token: str | None) -> list[ToolDefinition]:
        async with self.session(endpoint, token) as client:
            tools = list((await self._list_tools(client)).values())
        if token and any(token in tool.model_dump_json() for tool in tools):
            raise protocol_error()
        return tools

    @staticmethod
    async def _list_tools(client: _Session) -> dict[str, ToolDefinition]:
        tools: dict[str, ToolDefinition] = {}
        cursor: str | None = None
        for _ in range(MAX_DISCOVERY_PAGES):
            page = await client.request('tools/list', {'cursor': cursor} if cursor else {})
            items = page.get('tools')
            if not isinstance(items, list) or len(tools) + len(items) > MAX_TOOLS:
                raise protocol_error()
            for item in items:
                tool = _tool_definition(item)
                if tool.name in tools:
                    raise protocol_error()
                tools[tool.name] = tool
            next_cursor = page.get('nextCursor')
            if next_cursor is None:
                return tools
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > MAX_CURSOR
                or next_cursor == cursor
            ):
                raise protocol_error()
            cursor = next_cursor
        raise protocol_error()

    async def call(self, endpoint: str, token: str | None, tool: ToolDefinition, arguments: dict[str, object]) -> str:
        return await self._bounded(self._call(endpoint, token, tool, arguments))

    async def _call(self, endpoint: str, token: str | None, tool: ToolDefinition, arguments: dict[str, object]) -> str:
        result = await self._invoke(endpoint, token, tool, arguments)
        if result.get('isError') is True:
            raise AppError('mcp_tool_failed', 'MCP 工具返回执行失败，请核对参数或服务状态', 502)
        content = result.get('content')
        texts = (
            [
                item['text']
                for item in content
                if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str)
            ]
            if isinstance(content, list)
            else []
        )
        if isinstance(result.get('structuredContent'), dict):
            texts = [json.dumps(result['structuredContent'], ensure_ascii=True)]
        if not texts:
            raise AppError('mcp_result_unsupported', '此工具未返回可读取的文本或 JSON 对象', 502)
        text = '\n'.join(texts)
        if token and token in text:
            text = text.replace(token, '[凭据已隐藏]')
        return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + '\n[结果已截断]'

    async def invoke(
        self, endpoint: str, token: str | None, tool: ToolDefinition, arguments: dict[str, object]
    ) -> ExternalToolResult:
        result = await self._bounded(self._invoke(endpoint, token, tool, arguments))
        try:
            reply = ExternalToolResult.model_validate(result)
            encoded = json.dumps(reply.model_dump(exclude_none=True, by_alias=True), allow_nan=False)
        except (ValidationError, ValueError, RecursionError):
            raise protocol_error() from None
        if token and json.dumps(token)[1:-1] in encoded:
            raise protocol_error()
        return reply

    async def _invoke(
        self, endpoint: str, token: str | None, tool: ToolDefinition, arguments: dict[str, object]
    ) -> dict[str, object]:
        async with self.session(endpoint, token) as client:
            if (await self._list_tools(client)).get(tool.name) != tool:
                raise AppError('mcp_definition_changed', 'MCP 工具定义已变化，请管理员同步并重新审核', 409)
            return await client.request('tools/call', {'name': tool.name, 'arguments': arguments})

    @staticmethod
    async def _bounded(operation: Awaitable[T]) -> T:
        try:
            return await asyncio.wait_for(operation, 30)
        except (TimeoutError, asyncio.TimeoutError):
            raise AppError('mcp_timeout', 'MCP 服务响应超时', 504) from None


def _tool_definition(item: object) -> ToolDefinition:
    if not isinstance(item, dict):
        raise protocol_error()
    hints = item.get('annotations')
    hint = hints.get('readOnlyHint') if isinstance(hints, dict) else None
    try:
        return ToolDefinition.model_validate(
            {
                'name': item.get('name'),
                'description': item.get('description', ''),
                'input_schema': item.get('inputSchema'),
                'read_only': hint is True,
                'read_only_hint': hint if type(hint) is bool else None,
            }
        )
    except (ValidationError, ValueError, RecursionError):
        raise protocol_error() from None
