"""One app, real APIRoutes/DTOs, separate protocol documents."""

from copy import deepcopy
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel

from zhiheng.api.common.examples import add_examples
from zhiheng.api.common.transport import FILE_LIMIT, MULTIPART_LIMIT


class ResourceError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None
    request_id: str


class ResourceErrorEnvelope(BaseModel):
    error: ResourceError


class InferenceError(BaseModel):
    message: str
    type: str
    param: str | None
    code: str


class InferenceErrorEnvelope(BaseModel):
    error: InferenceError


class ConsoleError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ConsoleErrorEnvelope(BaseModel):
    error: ConsoleError


def header(description: str) -> dict[str, Any]:
    return {'description': description, 'schema': {'type': 'string'}}


def error_schemas(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(ref_template='#/components/schemas/{model}')
    definitions = schema.pop('$defs', {})
    return {**definitions, model.__name__: schema}


def _describe_preconditions(operation: dict[str, Any], responses: dict[str, Any]) -> None:
    params = operation.get('parameters', [])
    for param in params:
        if param['name'] == 'If-Match':
            param['required'] = True
            param['description'] = '当前资源的强 ETag；缺少返回 428，陈旧返回 412。'
            for status in (412, 428):
                responses[str(status)] = {
                    'description': HTTPStatus(status).phrase,
                    'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ResourceErrorEnvelope'}}},
                }
        elif param['name'] == 'Idempotency-Key':
            param['description'] = (
                '单个 UUID。相同应用、操作及键永久标识一次请求；响应快照保留 24 小时，过期重放返回 409 和当前允许的原结果引用。'
            )


def _describe_range(responses: dict[str, Any]) -> None:
    responses['200']['content'] = {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}
    for content_status in ('200', '206'):
        response = responses.setdefault(content_status, deepcopy(responses['200']))
        response['description'] = '文件内容' if content_status == '200' else '请求的字节范围'
        response['headers'].update(
            {
                key: header(description)
                for key, description in {
                    'Content-Range': '206 时的字节范围及文件总长。',
                    'Accept-Ranges': 'bytes',
                    'Content-Length': '本次返回的字节数。',
                    'ETag': '文件强状态版本 ETag，与该文件的元数据版本一致，可用于 If-Match。',
                }.items()
            }
        )
        response['headers']['ETag']['example'] = '"file-1"'
    responses['416'] = {
        'description': '无效或不可满足的单范围请求',
        'headers': {
            'Content-Range': header('bytes */总长'),
            'X-Request-ID': header('服务端请求 ID'),
        },
        'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ResourceErrorEnvelope'}}},
    }


def _describe_resource(operation: dict[str, Any]) -> None:
    responses = operation['responses']
    for status in (400, 401, 403, 404, 409, 413, 415, 422, 429, 500, 503):
        responses[str(status)] = {
            'description': HTTPStatus(status).phrase,
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ResourceErrorEnvelope'}}},
        }
    _describe_preconditions(operation, responses)
    for status, response in responses.items():
        headers = response.setdefault('headers', {})
        headers['X-Request-ID'] = header('服务端请求 ID，错误 envelope 中为同一值。')
        if status == '429':
            headers['Retry-After'] = header('再次请求前等待的秒数。')
        if status == '401':
            headers['WWW-Authenticate'] = header('Bearer')
        if status in {'201', '202'}:
            headers['Location'] = header('新资源或原生异步 operation 的相对 URL。')
        if status.startswith('2') and 'ETag' in operation.get('x-response-headers', []):
            headers['ETag'] = header('当前资源的强版本 ETag；修改时原样发送 If-Match。')
    if operation.get('x-supports-range'):
        _describe_range(responses)
    if operation.get('x-transport-policy') == 'multipart_file':
        operation['description'] = (
            f'仅接受一个二进制 file 和一个 purpose 字段。文件最多 {FILE_LIMIT} 字节；含 multipart 开销的请求最多 {MULTIPART_LIMIT} 字节。'
        )
    if operation.get('x-transport-policy') == 'bodyless':
        operation['description'] = (operation.get('description', '') + '\n此操作不发送请求体。').strip()


def _describe_inference(operation: dict[str, Any], path: str) -> None:
    responses = operation['responses']
    if operation.get('x-streaming'):
        operation['description'] = (
            'stream=true 返回 SSE data 帧；正常结束为 data: [DONE]，流内失败为推理错误对象。客户端断开沿用原 Run 取消策略。'
        )
        responses['200'].setdefault('content', {})['text/event-stream'] = {'schema': {'type': 'string'}}
    responses.pop('422', None)
    for status in (400, 401, 403, 404, 409, 413, 422, 429, 500, 502, 503):
        if status == HTTPStatus.UNPROCESSABLE_ENTITY and '/agents/' not in path:
            continue
        responses[str(status)] = {
            'description': HTTPStatus(status).phrase,
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/InferenceErrorEnvelope'}}},
        }


def _describe_console(operation: dict[str, Any]) -> None:
    responses = operation['responses']
    if operation.get('x-transport-policy') in {'binary_file', 'binary_skill'}:
        operation['requestBody'] = {
            'required': True,
            'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}},
        }
    for status in (401, 403, 409, 422):
        responses[str(status)] = {
            'description': HTTPStatus(status).phrase,
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ConsoleErrorEnvelope'}}},
        }


def describe_protocols(document: dict[str, Any]) -> dict[str, Any]:
    schemas = document.setdefault('components', {}).setdefault('schemas', {})
    for model in (ResourceErrorEnvelope, InferenceErrorEnvelope, ConsoleErrorEnvelope):
        schemas.update(error_schemas(model))
    for path, methods in document['paths'].items():
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            if path.startswith('/api/v1/'):
                _describe_resource(operation)
            elif path.startswith('/v1/'):
                _describe_inference(operation, path)
            elif operation.get('security'):
                _describe_console(operation)
    return document


def install_openapi(app: FastAPI) -> None:
    def generate(prefix: str | None = None) -> dict[str, Any]:
        routes = [
            context.original_route
            for context in iter_route_contexts(app.routes)
            if isinstance(context.original_route, APIRoute)
            and (prefix is None or context.original_route.path.startswith(prefix))
        ]
        result = get_openapi(
            title='智衡资源 API v1' if prefix == '/api/v1/' else '智衡 AI',
            version='1.0.0',
            openapi_version='3.1.0',
            routes=routes,
        )
        result = describe_protocols(result)
        add_examples(result, routes)
        return result

    def global_schema() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = generate()
        return app.openapi_schema

    app.openapi = global_schema  # type: ignore[method-assign]

    @app.get('/openapi/resources-v1.json', include_in_schema=False)
    async def resource_schema() -> dict[str, Any]:
        return generate('/api/v1/')

    @app.get('/openapi/inference-v1.json', include_in_schema=False)
    async def inference_schema() -> dict[str, Any]:
        return generate('/v1/')

    @app.get('/docs/resources', include_in_schema=False)
    async def resource_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url='/openapi/resources-v1.json', title='智衡资源 API v1')

    @app.get('/docs/inference', include_in_schema=False)
    async def inference_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url='/openapi/inference-v1.json', title='智衡推理 API')
