import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from zhiheng.api.common.openapi import install_openapi
from zhiheng.api.main import create_app
from zhiheng.bootstrap.settings import Settings

METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def documented_operations(schema: dict) -> set[tuple[str, str]]:
    return {
        (method, path) for path, operations in schema['paths'].items() for method in operations if method in METHODS
    }


@pytest.fixture(scope='module')
def platform_app() -> FastAPI:
    return create_app(
        Settings(
            database_url='postgresql+asyncpg://test:test@127.0.0.1:1/test',
            redis_url='redis://127.0.0.1:1/0',
            credential_key=Fernet.generate_key().decode(),
        )
    )


def test_global_schema_uses_effective_paths_for_all_registered_operations(platform_app: FastAPI) -> None:
    expected = {
        (method.lower(), context.path_format)
        for context in iter_route_contexts(platform_app.routes)
        if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema
        for method in context.methods or set()
    }
    schema = platform_app.openapi()
    assert documented_operations(schema) == expected
    for resource in ('users', 'roles', 'departments'):
        assert f'/api/admin/{resource}' in schema['paths']
        assert f'/api/admin/{resource}/{{identifier}}' in schema['paths']
        assert f'/{resource}' not in schema['paths']
        assert f'/{resource}/{{identifier}}' not in schema['paths']


def test_regenerating_schema_preserves_registered_routes_and_operation_ids(platform_app: FastAPI) -> None:
    def original_paths() -> list[str]:
        return [
            context.original_route.path
            for context in iter_route_contexts(platform_app.routes)
            if isinstance(context.original_route, APIRoute)
        ]

    before = original_paths()
    first = platform_app.openapi()
    platform_app.openapi_schema = None
    second = platform_app.openapi()
    assert first == second
    assert original_paths() == before
    ids = [operation['operationId'] for operations in second['paths'].values() for operation in operations.values()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ('prefix', 'document'),
    [('/api/v1', '/openapi/resources-v1.json'), ('/v1', '/openapi/inference-v1.json')],
)
async def test_grouped_schema_and_curl_samples_keep_nested_router_prefix(prefix: str, document: str) -> None:
    child = APIRouter()

    @child.get('/models')
    async def models() -> dict[str, str]:
        return {'status': 'ok'}

    parent = APIRouter(prefix=prefix)
    parent.include_router(child)
    app = FastAPI()
    app.include_router(parent)
    install_openapi(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(document)
    assert response.status_code == 200
    schema = response.json()
    expected_path = prefix + '/models'
    assert documented_operations(schema) == {('get', expected_path)}
    assert '{{ORIGIN}}' + expected_path in schema['paths'][expected_path]['get']['x-codeSamples'][0]['source']


def test_all_resource_operations_use_one_standard_example_with_optional_request_key(platform_app: FastAPI) -> None:
    schema = platform_app.openapi()
    operations = [
        operation
        for path, methods in schema['paths'].items()
        if path.startswith('/api/v1/')
        for operation in methods.values()
        if any(param['name'] == 'Idempotency-Key' for param in operation.get('parameters', []))
    ]
    assert len(operations) == 12
    for operation in operations:
        parameter = next(param for param in operation['parameters'] if param['name'] == 'Idempotency-Key')
        assert parameter['required'] is False
        assert '省略' in parameter['description'] and '24' in parameter['description']
        samples = operation['x-codeSamples']
        assert len(samples) == 1
        assert samples[0]['lang'] == 'Shell' and samples[0]['label'] == 'curl'
        assert 'Idempotency-Key' not in samples[0]['source']
        assert 'REQUEST_ID' not in samples[0]['source']


def test_knowledge_download_documents_binary_file_with_original_media_type(platform_app: FastAPI) -> None:
    operation = platform_app.openapi()['paths']['/api/v1/knowledge-bases/{base_id}/documents/{document_id}/download'][
        'get'
    ]
    response = operation['responses']['200']
    assert 'application/json' not in response['content']
    assert response['content']['application/octet-stream']['schema']['format'] == 'binary'
    assert 'Content-Disposition' in response['headers']
    assert 'MIME' in operation['description'] or 'Content-Type' in operation['description']


def test_knowledge_agent_example_preserves_request_uuid_and_documents_continuation(platform_app: FastAPI) -> None:
    operation = platform_app.openapi()['paths']['/v1/agents/{agent_id}/chat/completions']['post']
    parameter = next(p for p in operation['parameters'] if p['name'] == 'Idempotency-Key')
    assert parameter['in'] == 'header' and parameter['required'] is False
    assert 'client_request_id' in parameter['description']
    assert 'polling_url' in operation['description'] and 'admitted' in operation['description']
    example = operation['x-codeSamples'][0]['source']
    assert 'REQUEST_ID=${REQUEST_ID:-' in example
    assert 'Idempotency-Key: $REQUEST_ID' in example
    assert 'uuid.uuid4()' in example


def test_delete_knowledge_library_explicitly_documents_reference_confirmation(platform_app: FastAPI) -> None:
    operation = platform_app.openapi()['paths']['/api/v1/knowledge-bases/{base_id}']['delete']
    parameter = next(p for p in operation['parameters'] if p['name'] == 'confirm_references')
    assert parameter['schema']['default'] is False
    assert 'knowledge_referenced' in parameter['description']
    assert 'confirm_references=true' in operation['description']
    assert 'confirm_references=true' not in operation['x-codeSamples'][0]['source']
