"""Stateless MCP Streamable HTTP for explicitly published tools."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.api.common.auth import bearer
from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.modules.mcp.public import ToolSelection
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/v1', tags=['MCP'], dependencies=[Depends(HTTPBearer(auto_error=False))])
PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26')
MAX_REQUEST_BYTES = 32_768
MAX_TOOL_NAME_LENGTH = 120
RequestId = int | str | None


def failure(request_id: RequestId, code: int, message: str, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}, status_code=status
    )


async def read_message(request: Request) -> object:
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_REQUEST_BYTES:
            raise AppError('mcp_request_too_large', 'MCP 请求过大', 413)
        payload.extend(chunk)
    return json.loads(payload)


@router.post(
    '/mcp',
    summary='MCP 工具调用',
    description=(
        '使用带 mcp:invoke 的 Bearer Key。tools/list 返回当前 Key 获准调用的工具，'
        'tools/call 使用列表中的 name 和符合 inputSchema 的 arguments 执行工具。'
        '支持 initialize 协商协议版本和 ping。方法级错误见 JSON-RPC error 字段。'
    ),
    responses={
        200: {'description': 'JSON-RPC 响应，包含 result 或 error。'},
        202: {'description': '已接收无 id 的通知，响应正文为空。'},
    },
    openapi_extra={
        'x-required-scopes': ['mcp:invoke'],
        'requestBody': {
            'required': True,
            'content': {
                'application/json': {
                    'schema': {
                        'type': 'object',
                        'required': ['jsonrpc', 'method'],
                        'properties': {
                            'jsonrpc': {'type': 'string', 'enum': ['2.0']},
                            'id': {
                                'anyOf': [{'type': 'integer'}, {'type': 'string'}],
                                'description': '请求标识。发送通知时省略。',
                            },
                            'method': {'type': 'string', 'description': 'JSON-RPC 方法名，如 tools/list。'},
                            'params': {
                                'type': 'object',
                                'description': 'tools/call 需提供 name，arguments 默认为空对象。',
                            },
                        },
                    }
                }
            },
        },
    },
)
async def streamable_http(  # noqa: PLR0911, PLR0912 - explicit MCP transport and dispatch checks
    request: Request, container: ContainerDependency
) -> Response:
    origin = request.headers.get('origin')
    allowed_origins = {str(request.base_url).rstrip('/'), *container.settings.allowed_origins}
    if origin is not None and origin.rstrip('/') not in allowed_origins:
        return failure(None, -32600, 'Origin is not allowed', status=403)
    token = bearer(request)
    await container.database.require_schema()
    async with container.database.sessions.begin() as admission:
        identity = await container.keys.authenticate(admission, token, lock=True, scope='mcp:invoke')
        await container.resource_limits.consume(admission, identity)
    protocol = request.headers.get('mcp-protocol-version')
    if protocol is not None and protocol not in PROTOCOL_VERSIONS:
        return failure(None, -32600, 'Unsupported MCP protocol version', status=400)
    try:
        message = await read_message(request)
    except (ValueError, RecursionError):
        return failure(None, -32700, 'Parse error', status=400)
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return failure(None, -32600, 'Invalid Request', status=400)
    request_id = message.get('id')
    if 'id' in message and type(request_id) not in {str, int}:
        return failure(None, -32600, 'Invalid request ID', status=400)
    method = message.get('method')
    params = message.get('params', {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return failure(request_id, -32600, 'Invalid Request', status=400)
    if 'id' not in message:
        return Response(status_code=202)

    async def grants(session: AsyncSession) -> list[UUID]:
        await container.keys.authorize_identity(session, identity, (), scope='mcp:invoke')
        return (await container.keys.info_for_identity(session, identity)).mcp_tool_ids

    if method == 'initialize':
        version = params.get('protocolVersion')
        if not isinstance(version, str):
            return failure(request_id, -32602, 'protocolVersion is required')
        result = {
            'protocolVersion': version if version in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'DAIP MCP', 'version': '1.0'},
        }
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        async with container.database.sessions() as session:
            result = {
                'tools': await container.mcp.publications.tools(session, identity.application_id, await grants(session))
            }
    elif method == 'tools/call':
        name, arguments = params.get('name'), params.get('arguments', {})
        if not isinstance(name, str) or not name or len(name) > MAX_TOOL_NAME_LENGTH or not isinstance(arguments, dict):
            return failure(request_id, -32602, 'Invalid tool name or arguments')
        try:
            ToolSelection.bounded_arguments(arguments)
        except ValueError:
            return failure(request_id, -32602, 'Invalid or oversized tool arguments')
        try:
            reply = await container.mcp.publications.execute(
                identity.application_id,
                identity.key_id,
                identity.owner_id,
                name,
                arguments,
                grants,
            )
        except AppError as error:
            if error.status_code in {401, 403}:
                raise
            if error.code == 'mcp_tool_unavailable':
                return failure(request_id, -32602, 'Tool is unavailable')
            return failure(request_id, -32000, error.message)
        result = reply.model_dump(exclude_none=True, by_alias=True)
    else:
        return failure(request_id, -32601, 'Method not found')
    return JSONResponse({'jsonrpc': '2.0', 'id': request_id, 'result': result}, headers={'Cache-Control': 'no-store'})
