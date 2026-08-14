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
SENSITIVE_DETAIL = (
    'Authorization: Bearer management-secret at '
    'https://proxy.internal/credentials?token=query-secret private-body runtime-secret'
)


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
        'list_deployments': {'self'},
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
    ('headers', 'expected_iterations'),
    (
        ({'content-length': str(64 * 1024 + 1)}, 0),
        ({'content-length': '-1'}, 0),
        ({'content-length': SENSITIVE_DETAIL}, 0),
        ({'content-encoding': 'gzip'}, 0),
    ),
)
async def test_management_error_rejects_unsafe_body_without_reading(
    headers: Mapping[str, str],
    expected_iterations: int,
) -> None:
    upstream = RecordingResponseStream([SENSITIVE_DETAIL.encode()])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers=headers)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMManagementUpstreamError) as raised:
            await client.list_credentials()
    finally:
        await client.aclose()

    assert upstream.iteration_count == expected_iterations
    assert upstream.closed is True
    assert_sanitized_management_error(raised.value)


@pytest.mark.asyncio
async def test_management_error_stops_reading_after_raw_body_limit() -> None:
    upstream = RecordingResponseStream([b'x' * (32 * 1024), b'y' * (32 * 1024 + 1), b'must-not-read'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMManagementUpstreamError):
            await client.list_credentials()
    finally:
        await client.aclose()

    assert upstream.iteration_count == len(upstream.chunks) - 1
    assert upstream.closed is True


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
