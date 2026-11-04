"""The resource document is generated from real registered routes and DTOs."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts


async def test_grouped_resources_contract_matches_registered_routes(admin: httpx.AsyncClient, app: FastAPI) -> None:
    result = await admin.get('/openapi/resources-v1.json')
    assert result.status_code == 200, result.text
    assert result.headers['content-type'].startswith('application/json')
    schema = result.json()
    assert schema['openapi'] == '3.1.0'
    actual = {
        (method.lower(), route.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(route := context.original_route, APIRoute) and route.path.startswith('/api/v1/')
        for method in route.methods
    }
    documented = {
        (method, path)
        for path, operations in schema['paths'].items()
        for method in operations
        if method in {'get', 'post', 'patch', 'delete', 'put'}
    }
    assert actual == documented
    assert schema['components']['securitySchemes']['ResourceBearer'] == {'type': 'http', 'scheme': 'bearer'}
    for operations in schema['paths'].values():
        for operation in operations.values():
            assert operation['security'] == [{'ResourceBearer': []}]
            assert operation['x-required-scopes']
            assert (
                'X-Request-ID'
                in operation['responses'][
                    '200' if '200' in operation['responses'] else next(iter(operation['responses']))
                ]['headers']
            )
            assert 'Retry-After' in operation['responses']['429']['headers']
            assert operation['responses']['422']['content']['application/json']['schema']['$ref'].endswith(
                '/ResourceErrorEnvelope'
            )
    from zhiheng.api.common.examples import EXAMPLE_BODIES

    routes = [
        context.original_route
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
    ]
    recipe_routes = {
        (route.endpoint.__module__.rsplit('.', 1)[-1], route.endpoint.__name__): route
        for route in routes
        if route.path.startswith(('/api/v1/', '/v1/'))
    }
    assert EXAMPLE_BODIES.keys() <= recipe_routes.keys()
    for operation in (operation for operations in schema['paths'].values() for operation in operations.values()):
        for sample in operation['x-codeSamples']:
            checked = await asyncio.to_thread(
                subprocess.run, ['bash', '-n'], input=sample['source'], text=True, capture_output=True
            )
            assert checked.returncode == 0, checked.stderr
        body = operation.get('requestBody', {}).get('content', {}).get('application/json')
        if body:
            assert 'example' in body
    upload = schema['paths']['/api/v1/files']['post']
    body = upload['requestBody']['content']['multipart/form-data']['schema']
    if '$ref' in body:
        body = schema['components']['schemas'][body['$ref'].rsplit('/', 1)[-1]]
    assert body['properties']['file']['format'] == 'binary'
    assert body['required'] == ['file', 'purpose']
    assert 'Idempotency-Key' in {item['name'] for item in upload['parameters']}
    mutable = schema['paths']['/api/v1/files/{file_id}']['delete']
    assert 'If-Match' in {item['name'] for item in mutable['parameters']}
    assert '428' in mutable['responses'] and '412' in mutable['responses']
    assert (
        'Location' in schema['paths']['/api/v1/knowledge-bases/{base_id}/query']['post']['responses']['202']['headers']
    )
    assert (await admin.get('/docs/resources')).status_code == 200
    global_schema = (await admin.get('/openapi.json')).json()
    assert global_schema['paths']['/api/developer/applications']['get']['security'] == [{'ConsoleCookie': []}]
    assert not global_schema['paths']['/health/live']['get'].get('security')
    assert not global_schema['paths']['/api/auth/login']['post'].get('security')
    assert '/v1/files' not in global_schema['paths']
    evidence = os.environ.get('ZHIHENG_TEST_EVIDENCE_DIR')
    if evidence:
        await asyncio.to_thread(save_schema, Path(evidence), 'resources-v1.json', schema)
        inference = await admin.get('/openapi/inference-v1.json')
        assert inference.status_code == 200, inference.text
        await asyncio.to_thread(save_schema, Path(evidence), 'inference-v1.json', inference.json())


def save_schema(directory: Path, name: str, schema: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(schema, ensure_ascii=False, indent=2))


async def test_inference_group_documents_actual_early_body_errors(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        '/v1/chat/completions', content=b'x' * (1024 * 1024 + 1), headers={'Content-Type': 'application/json'}
    )
    assert response.status_code == 413
    schema = (await admin.get('/openapi/inference-v1.json')).json()
    errors = schema['paths']['/v1/chat/completions']['post']['responses']
    assert (
        errors['413']['content']['application/json']['schema']['$ref'] == '#/components/schemas/InferenceErrorEnvelope'
    )
    assert response.json()['error']['code'] == 'request_too_large'


async def test_immutable_skill_content_documents_original_binary_and_markdown(admin: httpx.AsyncClient) -> None:
    schema = (await admin.get('/openapi/resources-v1.json')).json()
    response = schema['paths']['/api/v1/skills/{skill_id}/versions/{revision_id}/content']['get']['responses']['200']
    assert response['content']['application/zip']['schema']['format'] == 'binary'
    assert response['content']['text/markdown']['schema']['type'] == 'string'
    assert 'ETag' in response['headers']


async def test_console_download_cookie_contract_preserves_denied_audit(
    admin: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    uploaded = await admin.post(
        '/api/files?filename=cookie-contract.txt',
        content=b'cookie contract',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    endpoint = '/api/files/' + uploaded.json()['id']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        denied = await anonymous.get(endpoint + '/download')
        assert denied.status_code == 401, denied.text
        schema = (await anonymous.get('/openapi.json')).json()
    records = (await admin.get(endpoint + '/audit')).json()
    denied_records = [row for row in records if row['action'] == 'download' and row['details']['outcome'] == 'denied']
    assert len(denied_records) == 1
    assert denied_records[0]['details']['bytes_sent'] == 0
    assert denied_records[0]['details']['error_code'] == denied.json()['error']['code']
    assert (await admin.get(endpoint + '/download')).status_code == 200
    assert not schema['paths']['/api/auth/login']['post'].get('security')
    assert not schema['paths']['/api/shared/messages/{token}']['get'].get('security')
    operation = schema['paths']['/api/files/{file_id}/download']['get']
    assert operation.get('security') == [{'ConsoleCookie': []}]
    assert operation['responses']['401']['content']['application/json']['schema']['$ref'].endswith(
        '/ConsoleErrorEnvelope'
    )


async def test_resource_file_range_version_etag_contract_is_distinct_from_skill_content_hash(
    admin: httpx.AsyncClient,
) -> None:
    from tests.core.integration.test_resource_files import auth, post
    from tests.core.integration.test_resource_key_scopes import application, issue

    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['files:read', 'files:write'])
    uploaded = await post(admin, key)
    assert uploaded.status_code == 201, uploaded.text
    resource = uploaded.json()
    endpoint = '/api/v1/files/' + resource['id']
    full = await admin.get(endpoint + '/content', headers=auth(key))
    partial = await admin.get(endpoint + '/content', headers={**auth(key), 'Range': 'bytes=0-1'})
    metadata = await admin.get(endpoint, headers=auth(key))
    assert full.status_code == 200 and partial.status_code == 206
    assert (
        full.headers['etag'] == partial.headers['etag'] == metadata.headers['etag'] == f'"file-{resource["version"]}"'
    )
    schema = (await admin.get('/openapi/resources-v1.json')).json()
    responses = schema['paths']['/api/v1/files/{file_id}/content']['get']['responses']
    for status in ('200', '206'):
        tag = responses[status]['headers']['ETag']
        assert 'SHA256' not in tag['description'] and '版本' in tag['description']
        assert tag['example'] == full.headers['etag']
    skill_content = schema['paths']['/api/v1/skills/{skill_id}/versions/{revision_id}/content']['get']['responses'][
        '200'
    ]
    assert 'SHA256' in skill_content['headers']['ETag']['description']
