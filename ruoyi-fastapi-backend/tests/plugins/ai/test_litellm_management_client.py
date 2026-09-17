import hashlib
import inspect
import json
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

import httpx
import pytest

from middlewares.trace_middleware.ctx import TraceCtx
from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.infrastructure.litellm_management_errors import (
    LiteLLMManagementAuthenticationError,
    LiteLLMManagementError,
    LiteLLMManagementMisconfiguredError,
    LiteLLMManagementTimeoutError,
    LiteLLMManagementUnavailableError,
    LiteLLMManagementUpstreamError,
)

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]
OVERSIZED_ERROR_BODY_BYTES = 64 * 1024 + 1
SENSITIVE_DETAIL = (
    'Authorization: Bearer management-secret at '
    'https://proxy.internal/credentials?token=query-secret private-body runtime-secret'
)


@pytest.mark.asyncio
async def test_runtime_key_access_uses_fixed_hashed_key_and_preserves_other_settings() -> None:
    key_hash = hashlib.sha256(b'runtime-secret').hexdigest()
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['authorization'] == 'Bearer management-secret'
        assert not request.url.query
        body = json.loads(request.content)
        assert 'runtime-secret' not in request.content.decode()
        calls.append((request.method, request.url.path, body))
        if request.url.path == '/v2/key/info':
            return httpx.Response(200, json={'info': [{'models': ['old-model'], 'max_budget': 10}]})
        return httpx.Response(200, json={'models': body['models']})

    client = build_client(handler)
    try:
        assert await client.get_runtime_key_models() == ['old-model']
        await client.update_runtime_key_models(['old-model', 'new-model'])
    finally:
        await client.aclose()
    assert calls == [
        ('POST', '/v2/key/info', {'keys': [key_hash]}),
        ('POST', '/key/update', {'key': key_hash, 'models': ['old-model', 'new-model']}),
    ]


@pytest.mark.asyncio
async def test_deployment_lookup_filters_alias_instead_of_only_checking_first_page() -> None:
    alias = 'provider/model&region=china'

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v2/model/info'
        assert request.headers['authorization'] == 'Bearer management-secret'
        if not request.url.query:
            return httpx.Response(200, json={'data': [{'model_name': f'first-{i}'} for i in range(50)]})
        assert dict(request.url.params) == {'model': alias}
        return httpx.Response(200, json={'data': [{'model_name': alias}], 'total_count': 1})

    client = build_client(handler)
    try:
        assert await client.model_deployment_exists(alias)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_deployment_lookup_still_requires_exact_matching_alias() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'data': [{'model_name': 'different-model'}]})

    client = build_client(handler)
    try:
        assert not await client.model_deployment_exists('requested-model')
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('model_alias', (None, '', 'auto-model', 'provider/model&region=china#value?x=+%', '中文 模型'))
async def test_deployment_list_supports_optional_encoded_alias_filter(model_alias: str | None) -> None:
    response = {'data': [{'model_name': model_alias or 'first-page-model'}], 'total_count': 1}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == 'GET'
        assert request.url.path == '/v2/model/info'
        assert request.headers['authorization'] == 'Bearer management-secret'
        assert dict(request.url.params) == ({'model': model_alias} if model_alias else {})
        return httpx.Response(200, json=response)

    client = build_client(handler)
    try:
        assert await client.list_deployments(model_alias=model_alias) == response
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['read', 'write', 'deployment'])
async def test_key_sync_transport_failure_never_exposes_credentials(method: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(SENSITIVE_DETAIL, request=request)

    client = build_client(handler)
    calls = {
        'read': client.get_runtime_key_models,
        'write': lambda: client.update_runtime_key_models(['new-one']),
        'deployment': lambda: client.model_deployment_exists('new-one'),
    }
    try:
        with pytest.raises(LiteLLMManagementTimeoutError) as error:
            await calls[method]()
        assert_sanitized_management_error(error.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {},
        {'info': []},
        {'info': [{}]},
        {'info': [{'models': None}]},
        {'info': [{'models': ['ok', 1]}]},
        {'info': [{'models': []}, {'models': []}]},
    ],
)
async def test_runtime_key_access_rejects_malformed_metadata(body: dict) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMManagementError) as error:
            await client.get_runtime_key_models()
        assert_sanitized_management_error(error.value)
    finally:
        await client.aclose()


def build_client(handler: Handler, *, management_key: str = 'management-secret') -> LiteLLMManagementClient:
    settings = LiteLLMSettings(
        LITELLM_BASE_URL='https://proxy.example.com',
        LITELLM_RUNTIME_KEY='runtime-secret',
        LITELLM_MANAGEMENT_KEY=management_key,
    )
    return LiteLLMManagementClient(settings, transport=httpx.MockTransport(handler))


def assert_sanitized_management_error(error: Exception) -> None:
    serialized = repr(
        (
            str(error),
            repr(error),
            vars(error),
            error.args,
            error.__cause__,
            error.__context__,
            ''.join(traceback.format_exception(error)),
        )
    ).lower()
    assert error.__cause__ is None
    assert error.__context__ is None
    for secret in (
        'authorization',
        'management-secret',
        'runtime-secret',
        'proxy.internal',
        'query-secret',
        'private-body',
        'credential-value-secret',
    ):
        assert secret not in serialized

    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename.replace('\\', '/')
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            for value in current.tb_frame.f_locals.values():
                assert not isinstance(value, (httpx.Request, httpx.Response))
                local_repr = repr(value).lower()
                for secret in (
                    'authorization',
                    'management-secret',
                    'runtime-secret',
                    'proxy.internal',
                    'query-secret',
                    'private-body',
                    'credential-value-secret',
                ):
                    assert secret not in local_repr
        current = current.tb_next


class RecordingResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self.chunks = chunks
        self.iteration_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.iteration_count += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_public_management_reads_do_not_send_either_key() -> None:
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert 'authorization' not in request.headers
        return httpx.Response(200, json=[] if request.url.path == '/public/providers/fields' else 'healthy')

    client = build_client(handler, management_key='')
    try:
        assert await client.list_provider_fields() == {'items': []}
        assert await client.check_liveliness() == {'status': 'healthy'}
    finally:
        await client.aclose()

    assert requests == [('GET', '/public/providers/fields'), ('GET', '/health/liveliness')]


@pytest.mark.asyncio
async def test_protected_management_methods_use_management_key_and_exact_contracts() -> None:
    create_credential = {
        'credential_name': 'credential-1',
        'credential_info': {'description': 'main'},
        'credential_values': {'api_key': 'credential-value-secret'},
    }
    rotate_credential = {
        'credential_name': 'credential-1',
        'credential_info': {'description': 'rotated'},
        'credential_values': {'api_key': 'new-value'},
    }
    create_deployment = {
        'model_name': 'company-chat',
        'litellm_params': {'model': 'openai/test'},
        'model_info': {'description': 'primary'},
    }
    update_deployment = {'blocked': True, 'model_info': {'description': 'paused'}}
    test_payload = {
        'mode': 'chat',
        'litellm_params': {'model': 'openai/test'},
        'model_info': {},
    }
    expected = [
        ('GET', '/credentials', None),
        ('GET', '/credentials/by_name/credential-1', None),
        ('POST', '/credentials', create_credential),
        ('PATCH', '/credentials/credential-1', rotate_credential),
        ('DELETE', '/credentials/credential-1', None),
        ('GET', '/v2/model/info', None),
        ('POST', '/model/new', create_deployment),
        ('PATCH', '/model/model-1/update', update_deployment),
        ('POST', '/model/delete', {'id': 'model-1'}),
        ('POST', '/health/test_connection', test_payload),
    ]
    received: list[tuple[str, str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['authorization'] == 'Bearer management-secret'
        assert request.headers['authorization'] != 'Bearer runtime-secret'
        body = None if not request.content else request.read().decode()
        received.append((request.method, request.url.path, None if body is None else json.loads(body)))
        return httpx.Response(200, json={'ok': True})

    client = build_client(handler)
    try:
        await client.list_credentials()
        await client.get_credential('credential-1')
        await client.create_credential(create_credential)
        await client.rotate_credential('credential-1', rotate_credential)
        await client.delete_credential('credential-1')
        await client.list_deployments()
        await client.create_deployment(create_deployment)
        await client.update_deployment('model-1', update_deployment)
        await client.delete_deployment('model-1')
        await client.test_connection(test_payload)
    finally:
        await client.aclose()

    assert received == expected


def test_management_method_signatures_do_not_expose_transport_escape_hatches() -> None:
    expected_parameters = {
        'list_provider_fields': {'self'},
        'list_credentials': {'self'},
        'get_credential': {'self', 'credential_name'},
        'create_credential': {'self', 'payload'},
        'rotate_credential': {'self', 'credential_name', 'payload'},
        'delete_credential': {'self', 'credential_name'},
        'list_deployments': {'self', 'model_alias'},
        'create_deployment': {'self', 'payload'},
        'update_deployment': {'self', 'model_id', 'payload'},
        'delete_deployment': {'self', 'model_id'},
        'test_connection': {'self', 'payload'},
        'check_liveliness': {'self'},
        'aclose': {'self'},
    }

    for method_name, parameters in expected_parameters.items():
        signature = inspect.signature(getattr(LiteLLMManagementClient, method_name))
        assert set(signature.parameters) == parameters
        assert not {'headers', 'authorization', 'url', 'credential'} & set(signature.parameters)


@pytest.mark.asyncio
async def test_list_responses_wrap_top_level_arrays_but_preserve_objects() -> None:
    responses: list[object] = [[{'credential_name': 'a'}], {'data': [{'model_name': 'b'}]}]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    client = build_client(handler)
    try:
        assert await client.list_credentials() == {'items': [{'credential_name': 'a'}]}
        assert await client.list_deployments() == {'data': [{'model_name': 'b'}]}
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method_name', 'identifier', 'expected_raw_path'),
    (
        (
            'get_credential',
            'credential/with?query#fragment%value\n',
            b'/credentials/by_name/credential%2Fwith%3Fquery%23fragment%25value%0A',
        ),
        (
            'rotate_credential',
            'credential/with?query#fragment%value\n',
            b'/credentials/credential%2Fwith%3Fquery%23fragment%25value%0A',
        ),
        (
            'delete_credential',
            'credential/with?query#fragment%value\n',
            b'/credentials/credential%2Fwith%3Fquery%23fragment%25value%0A',
        ),
        (
            'update_deployment',
            'model/with?query#fragment%value\n',
            b'/model/model%2Fwith%3Fquery%23fragment%25value%0A/update',
        ),
    ),
)
async def test_dynamic_identifiers_are_encoded_as_single_path_segments(
    method_name: str,
    identifier: str,
    expected_raw_path: bytes,
) -> None:
    network_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        assert request.url.raw_path == expected_raw_path
        assert not request.url.query
        return httpx.Response(200, json={})

    client = build_client(handler)
    try:
        method = getattr(client, method_name)
        if method_name in {'rotate_credential', 'update_deployment'}:
            await method(identifier, {})
        else:
            await method(identifier)
    finally:
        await client.aclose()

    assert network_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method_name', 'path_template'),
    (
        ('get_credential', '/credentials/by_name/{identifier}'),
        ('rotate_credential', '/credentials/{identifier}'),
        ('delete_credential', '/credentials/{identifier}'),
        ('update_deployment', '/model/{identifier}/update'),
    ),
)
@pytest.mark.parametrize(
    ('identifier', 'encoded_identifier'),
    (
        ('.', '%2E'),
        ('..', '%2E%2E'),
        ('foo.bar', 'foo%2Ebar'),
    ),
)
async def test_dot_identifiers_are_preserved_as_encoded_path_segments(
    method_name: str,
    path_template: str,
    identifier: str,
    encoded_identifier: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        expected_path = path_template.format(identifier=identifier)
        expected_raw_path = path_template.format(identifier=encoded_identifier).encode()
        assert request.url.path == expected_path
        assert request.url.raw_path == expected_raw_path
        assert not request.url.query
        return httpx.Response(200, json={})

    client = build_client(handler)
    try:
        method = getattr(client, method_name)
        if method_name in {'rotate_credential', 'update_deployment'}:
            await method(identifier, {})
        else:
            await method(identifier)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_dynamic_identifier_is_not_retained_when_request_fails() -> None:
    identifier = 'private-body/with?token=query-secret'

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(SENSITIVE_DETAIL, request=request)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMManagementUnavailableError) as raised:
            await client.get_credential(identifier)
    finally:
        await client.aclose()

    assert_sanitized_management_error(raised.value)


@pytest.mark.asyncio
async def test_protected_reads_and_writes_fail_fast_without_management_key() -> None:
    network_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx.Response(200, json={})

    client = build_client(handler, management_key='   ')
    calls = (
        client.list_credentials,
        lambda: client.get_credential('credential-1'),
        lambda: client.create_credential({}),
        lambda: client.rotate_credential('credential-1', {}),
        lambda: client.delete_credential('credential-1'),
        client.list_deployments,
        lambda: client.create_deployment({}),
        lambda: client.update_deployment('model-1', {}),
        lambda: client.delete_deployment('model-1'),
        lambda: client.test_connection({}),
        client.get_runtime_key_models,
        lambda: client.model_deployment_exists('new-one'),
        lambda: client.update_runtime_key_models(['only-one']),
    )
    try:
        for call in calls:
            with pytest.raises(LiteLLMManagementMisconfiguredError):
                await call()
    finally:
        await client.aclose()

    assert network_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', (401, 403))
async def test_management_auth_status_has_stable_mapping(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=SENSITIVE_DETAIL)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMManagementAuthenticationError) as raised:
            await client.list_credentials()
    finally:
        await client.aclose()

    assert_sanitized_management_error(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error_kind', 'error_type'),
    (
        ('timeout', LiteLLMManagementTimeoutError),
        ('connect', LiteLLMManagementUnavailableError),
    ),
)
async def test_management_transport_errors_have_stable_clean_mappings(
    error_kind: str,
    error_type: type[Exception],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if error_kind == 'timeout':
            raise httpx.ReadTimeout(SENSITIVE_DETAIL, request=request)
        raise httpx.ConnectError(SENSITIVE_DETAIL, request=request)

    client = build_client(handler)
    try:
        with pytest.raises(error_type) as raised:
            await client.create_credential(
                {
                    'credential_name': 'credential-1',
                    'credential_info': {},
                    'credential_values': {'api_key': 'credential-value-secret'},
                }
            )
    finally:
        await client.aclose()

    assert_sanitized_management_error(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('response_factory', 'error_type'),
    (
        (lambda: httpx.Response(500, text=SENSITIVE_DETAIL), LiteLLMManagementUpstreamError),
        (lambda: httpx.Response(200, text=SENSITIVE_DETAIL), LiteLLMManagementUpstreamError),
        (lambda: httpx.Response(200, json=['allowed-list']), None),
    ),
)
async def test_management_status_and_json_shape_are_normalized(
    response_factory: Callable[[], httpx.Response],
    error_type: type[Exception] | None,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response_factory()

    client = build_client(handler)
    try:
        if error_type is None:
            assert await client.list_credentials() == {'items': ['allowed-list']}
        else:
            with pytest.raises(error_type) as raised:
                await client.list_credentials()
            assert_sanitized_management_error(raised.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status_code', 'error_type'),
    (
        (401, LiteLLMManagementAuthenticationError),
        (500, LiteLLMManagementUpstreamError),
    ),
)
@pytest.mark.parametrize(
    ('chunks', 'headers'),
    (
        ((b'x' * OVERSIZED_ERROR_BODY_BYTES,), {}),
        ((b'first', b'second', b'must-not-read'), {}),
        ((SENSITIVE_DETAIL.encode(),), {'content-encoding': 'gzip'}),
    ),
)
async def test_management_error_never_reads_response_body(
    status_code: int,
    error_type: type[Exception],
    chunks: Sequence[bytes],
    headers: Mapping[str, str],
) -> None:
    upstream = RecordingResponseStream(chunks)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, stream=upstream, headers=headers)

    client = build_client(handler)
    try:
        with pytest.raises(error_type) as raised:
            await client.list_credentials()
    finally:
        await client.aclose()

    assert upstream.iteration_count == 0
    assert upstream.closed is True
    assert_sanitized_management_error(raised.value)


@pytest.mark.asyncio
async def test_management_requests_forward_trace_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TraceCtx, 'get_request_id', staticmethod(lambda: 'request-1'))
    monkeypatch.setattr(TraceCtx, 'get_trace_id', staticmethod(lambda: 'trace-1'))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['request-id'] == 'request-1'
        assert request.headers['trace-id'] == 'trace-1'
        assert request.headers['user-agent'] == 'RuoYi-AI-Platform/0.1'
        return httpx.Response(200, json={})

    client = build_client(handler)
    try:
        await client.list_credentials()
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ('error_type', 'status_code', 'code'),
    (
        (LiteLLMManagementMisconfiguredError, 503, 'LITELLM_MANAGEMENT_MISCONFIGURED'),
        (LiteLLMManagementUnavailableError, 503, 'LITELLM_MANAGEMENT_UNAVAILABLE'),
        (LiteLLMManagementTimeoutError, 504, 'LITELLM_MANAGEMENT_TIMEOUT'),
        (LiteLLMManagementAuthenticationError, 502, 'LITELLM_MANAGEMENT_AUTHENTICATION_FAILED'),
        (LiteLLMManagementUpstreamError, 502, 'LITELLM_MANAGEMENT_UPSTREAM_ERROR'),
    ),
)
def test_management_errors_have_stable_public_contract(
    error_type: type[LiteLLMManagementError],
    status_code: int,
    code: str,
) -> None:
    error = error_type()

    assert error.status_code == status_code
    assert error.code == code
    assert error.message
    assert 'secret' not in error.message.lower()
