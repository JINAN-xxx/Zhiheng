import pickle

import pytest
from pydantic import ValidationError

from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_developer_api_vo import RateCardCreateModel
from plugins.ai.entity.vo.ai_openai_compat_vo import (
    OpenAIChatCompletionChunkModel,
    OpenAIChatCompletionRequestModel,
    OpenAIChatCompletionResponseModel,
    OpenAIEmbeddingRequestModel,
    OpenAIEmbeddingResponseModel,
    OpenAIModelListResponseModel,
    OpenAIRerankRequestModel,
    OpenAIRerankResponseModel,
)

MODEL_CODE = 'deepseek-chat'
EMBEDDING_DIMENSIONS = 1024
EXPECTED_VECTOR_COUNT = 2
RERANK_TOP_N = 2


@pytest.mark.parametrize(
    'payload',
    (
        {
            'billingMode': 'token',
            'inputPricePerToken': '0.001',
            'outputPricePerToken': '0.002',
            'searchPricePerUnit': '0.1',
        },
        {
            'billingMode': 'search_unit',
            'inputPricePerToken': '0.001',
            'outputPricePerToken': '0',
            'searchPricePerUnit': '0.1',
        },
    ),
)
def test_rate_card_rejects_prices_from_the_other_billing_mode(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RateCardCreateModel.model_validate(payload)


@pytest.mark.parametrize(
    ('factory_name', 'status_code', 'code', 'error_type', 'message'),
    (
        ('feature_disabled', 404, 'not_found', 'invalid_request_error', 'The requested resource was not found.'),
        ('invalid_api_key', 401, 'invalid_api_key', 'authentication_error', 'The API key is invalid or inactive.'),
        ('permission_denied', 403, 'permission_denied', 'permission_error', 'The API key cannot access this model.'),
        ('model_not_found', 404, 'model_not_found', 'invalid_request_error', 'The requested model was not found.'),
        ('invalid_request', 400, 'invalid_request', 'invalid_request_error', 'The request is invalid.'),
        ('upstream_error', 502, 'upstream_error', 'api_error', 'The model provider request failed.'),
        ('service_unavailable', 503, 'service_unavailable', 'api_error', 'The model service is unavailable.'),
    ),
)
def test_public_api_errors_are_fixed_openai_compatible_and_pickle_safe(
    factory_name: str,
    status_code: int,
    code: str,
    error_type: str,
    message: str,
) -> None:
    error = getattr(PublicApiError, factory_name)()

    assert error.status_code == status_code
    assert error.code == code
    assert error.error_type == error_type
    assert error.public_message == message
    assert error.public_dict() == {
        'error': {
            'message': message,
            'type': error_type,
            'param': None,
            'code': code,
        }
    }
    assert pickle.loads(pickle.dumps(error)).public_dict() == error.public_dict()
    assert vars(error) == {}
    assert error.args == ()
    with pytest.raises(AttributeError):
        error.code = 'changed'


def test_public_api_error_rejects_direct_construction_and_subclassing() -> None:
    with pytest.raises(TypeError):
        PublicApiError(0)
    with pytest.raises(TypeError):

        class InvalidError(PublicApiError):
            pass


def test_model_list_response_uses_only_public_model_fields() -> None:
    response = OpenAIModelListResponseModel.model_validate(
        {
            'object': 'list',
            'data': [
                {
                    'id': MODEL_CODE,
                    'object': 'model',
                    'created': 1786982400,
                    'owned_by': 'zhiheng',
                    'platform_model_id': 'must-not-survive',
                    'binding_id': 'must-not-survive',
                }
            ],
        }
    )

    assert response.model_dump() == {
        'object': 'list',
        'data': [{'id': MODEL_CODE, 'object': 'model', 'created': 1786982400, 'owned_by': 'zhiheng'}],
    }


def valid_chat_request() -> dict[str, object]:
    return {
        'model': MODEL_CODE,
        'messages': [
            {'role': 'system', 'content': 'Be concise.'},
            {'role': 'user', 'content': 'Hello'},
        ],
        'stream': False,
        'temperature': 0.2,
        'top_p': 0.9,
        'max_tokens': 128,
        'presence_penalty': 0.1,
        'frequency_penalty': -0.1,
        'stop': ['END'],
        'user': 'caller-42',
    }


def test_chat_request_accepts_only_first_release_fields_and_snake_case() -> None:
    request = OpenAIChatCompletionRequestModel.model_validate(valid_chat_request())

    assert request.model == MODEL_CODE
    assert [message.role for message in request.messages] == ['system', 'user']
    assert request.stop == ['END']
    assert request.user == 'caller-42'
    assert request.model_dump(exclude_none=True) == valid_chat_request()


@pytest.mark.parametrize('stream', (False, True))
def test_chat_request_accepts_strict_stream_modes(stream: bool) -> None:
    payload = valid_chat_request()
    payload['stream'] = stream

    assert OpenAIChatCompletionRequestModel.model_validate(payload).stream is stream


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('provider', 'openai'),
        ('api_key', 'secret'),
        ('api_base', 'https://private.example'),
        ('custom_llm_provider', 'openai'),
        ('tools', []),
        ('response_format', {'type': 'json_object'}),
        ('n', 2),
    ),
)
def test_chat_request_rejects_unknown_or_private_fields(field: str, value: object) -> None:
    payload = valid_chat_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        OpenAIChatCompletionRequestModel.model_validate(payload)


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('stream', 1),
        ('stream', 'true'),
        ('temperature', -0.1),
        ('temperature', 2.1),
        ('top_p', 1.1),
        ('max_tokens', 0),
        ('presence_penalty', -2.1),
        ('frequency_penalty', 2.1),
        ('stop', []),
        ('stop', ['1', '2', '3', '4', '5']),
        ('user', ''),
    ),
)
def test_chat_request_rejects_unsupported_or_out_of_range_values(field: str, value: object) -> None:
    payload = valid_chat_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        OpenAIChatCompletionRequestModel.model_validate(payload)


@pytest.mark.parametrize('role', ('system', 'developer', 'user', 'assistant'))
def test_chat_request_accepts_supported_text_message_roles(role: str) -> None:
    payload = valid_chat_request()
    payload['messages'] = [{'role': role, 'content': 'hello'}]
    assert OpenAIChatCompletionRequestModel.model_validate(payload).messages[0].role == role


@pytest.mark.parametrize(
    'messages',
    (
        [],
        [{'role': 'tool', 'content': 'hello'}],
        [{'role': 'user', 'content': ''}],
        [{'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]}],
        [{'role': 'user', 'content': 'hello', 'name': 'unexpected'}],
    ),
)
def test_chat_request_rejects_empty_multimodal_or_unsupported_messages(messages: list[object]) -> None:
    payload = valid_chat_request()
    payload['messages'] = messages
    with pytest.raises(ValidationError):
        OpenAIChatCompletionRequestModel.model_validate(payload)


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('model', ' bad-model'),
        ('model', 'bad/model'),
        ('user', 'caller\nsecret'),
        ('stop', 'END\x00SECRET'),
    ),
)
def test_chat_request_rejects_control_or_unsafe_text(field: str, value: object) -> None:
    payload = valid_chat_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        OpenAIChatCompletionRequestModel.model_validate(payload)


def test_chat_completion_response_uses_openai_shape_and_ignores_internal_fields() -> None:
    response = OpenAIChatCompletionResponseModel.model_validate(
        {
            'id': 'chatcmpl_00000000000040008000000000000001',
            'object': 'chat.completion',
            'created': 1786982400,
            'model': MODEL_CODE,
            'choices': [
                {
                    'index': 0,
                    'message': {'role': 'assistant', 'content': 'Hello'},
                    'finish_reason': 'stop',
                }
            ],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4},
            'binding_id': 'must-not-survive',
            'provider_cost': 'must-not-survive',
        }
    )

    serialized = response.model_dump()
    assert serialized['object'] == 'chat.completion'
    assert serialized['choices'][0]['message'] == {'role': 'assistant', 'content': 'Hello'}
    assert serialized['usage'] == {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4}
    assert 'binding_id' not in serialized
    assert 'provider_cost' not in serialized


@pytest.mark.parametrize(
    ('delta', 'finish_reason'),
    (
        ({'role': 'assistant'}, None),
        ({'content': 'Hello'}, None),
        ({}, 'stop'),
    ),
)
def test_chat_completion_chunk_supports_role_content_and_finish_frames(
    delta: dict[str, str],
    finish_reason: str | None,
) -> None:
    chunk = OpenAIChatCompletionChunkModel.model_validate(
        {
            'id': 'chatcmpl_00000000000040008000000000000001',
            'object': 'chat.completion.chunk',
            'created': 1786982400,
            'model': MODEL_CODE,
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
            'platform_model_id': 'must-not-survive',
            'binding_id': 'must-not-survive',
        }
    )

    serialized = chunk.model_dump(exclude_none=True)
    assert serialized['object'] == 'chat.completion.chunk'
    assert serialized['choices'][0]['delta'] == delta
    assert 'platform_model_id' not in serialized
    assert 'binding_id' not in serialized


def valid_embedding_request() -> dict[str, object]:
    return {
        'model': 'company-embedding-v1',
        'input': ['first document', 'second document'],
        'encoding_format': 'float',
        'dimensions': EMBEDDING_DIMENSIONS,
        'user': 'external-user-42',
    }


def test_embedding_request_accepts_first_release_fields_and_snake_case() -> None:
    request = OpenAIEmbeddingRequestModel.model_validate(valid_embedding_request())

    assert request.model == 'company-embedding-v1'
    assert request.input == ['first document', 'second document']
    assert request.dimensions == EMBEDDING_DIMENSIONS
    assert request.user == 'external-user-42'
    assert request.model_dump(exclude_none=True) == valid_embedding_request()


def test_embedding_request_accepts_single_string_input() -> None:
    request = OpenAIEmbeddingRequestModel.model_validate({'model': 'company-embedding-v1', 'input': 'text'})

    assert request.input == 'text'


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('input', []),
        ('input', ['']),
        ('input', ['ok', '']),
        ('input', ['x' * 32769]),
        ('input', 42),
        ('encoding_format', 'base64'),
        ('dimensions', 0),
        ('dimensions', 65537),
        ('user', ''),
        ('user', 'bad\nvalue'),
        ('provider', 'openai'),
        ('api_key', 'secret'),
    ),
)
def test_embedding_request_rejects_invalid_or_private_fields(field: str, value: object) -> None:
    payload = valid_embedding_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        OpenAIEmbeddingRequestModel.model_validate(payload)


def test_embedding_request_rejects_more_than_256_inputs() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbeddingRequestModel.model_validate({'model': 'company-embedding-v1', 'input': ['x'] * 257})


def test_embedding_response_uses_openai_shape_and_ignores_internal_fields() -> None:
    response = OpenAIEmbeddingResponseModel.model_validate(
        {
            'object': 'list',
            'data': [
                {'object': 'embedding', 'index': 0, 'embedding': [0.0123, -0.0456]},
                {'object': 'embedding', 'index': 1, 'embedding': [0.5, 0.25]},
            ],
            'model': 'company-embedding-v1',
            'usage': {'prompt_tokens': 8, 'total_tokens': 8},
            'platform_model_id': 'must-not-survive',
            'binding_id': 'must-not-survive',
        }
    )

    serialized = response.model_dump()
    assert serialized['object'] == 'list'
    assert serialized['data'][0]['embedding'] == [0.0123, -0.0456]
    assert serialized['usage'] == {'prompt_tokens': 8, 'total_tokens': 8}
    assert 'platform_model_id' not in serialized
    assert 'binding_id' not in serialized


def test_embedding_response_rejects_non_finite_or_empty_vectors() -> None:
    base = {
        'object': 'list',
        'data': [{'object': 'embedding', 'index': 0, 'embedding': [0.1]}],
        'model': 'company-embedding-v1',
        'usage': {'prompt_tokens': 1, 'total_tokens': 1},
    }
    with pytest.raises(ValidationError):
        OpenAIEmbeddingResponseModel.model_validate(
            {**base, 'data': [{'object': 'embedding', 'index': 0, 'embedding': []}]}
        )
    with pytest.raises(ValidationError):
        OpenAIEmbeddingResponseModel.model_validate(
            {**base, 'data': [{'object': 'embedding', 'index': 0, 'embedding': [float('nan')]}]}
        )
    # 各向量自身必须非空且全部有限；跨向量维度一致性由 Adapter/领域层保证。
    mixed = OpenAIEmbeddingResponseModel.model_validate(
        {
            **base,
            'data': [
                {'object': 'embedding', 'index': 0, 'embedding': [0.1, 0.2]},
                {'object': 'embedding', 'index': 1, 'embedding': [0.3]},
            ],
        }
    )
    assert len(mixed.data) == EXPECTED_VECTOR_COUNT


def valid_rerank_request() -> dict[str, object]:
    return {
        'model': 'company-rerank-v1',
        'query': 'Which document describes the refund policy?',
        'documents': [
            'Shipping usually takes three days.',
            {'text': 'Refunds are accepted within thirty days.'},
        ],
        'top_n': RERANK_TOP_N,
        'return_documents': True,
    }


def test_rerank_request_accepts_first_release_fields_and_document_union() -> None:
    request = OpenAIRerankRequestModel.model_validate(valid_rerank_request())

    assert request.model == 'company-rerank-v1'
    assert request.documents[0] == 'Shipping usually takes three days.'
    assert request.documents[1].text == 'Refunds are accepted within thirty days.'
    assert request.top_n == RERANK_TOP_N
    assert request.return_documents is True


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('query', ''),
        ('query', 'x' * 8193),
        ('documents', []),
        ('documents', ['']),
        ('documents', [{'text': ''}]),
        ('documents', ['x' * 32769]),
        ('documents', [{'text': 'ok', 'rank_fields': ['title']}]),
        ('documents', [{'text': 'ok', 'extra': 1}]),
        ('top_n', 0),
        ('top_n', 3),
        ('return_documents', 'yes'),
        ('rank_fields', ['title']),
        ('max_chunks_per_doc', 10),
        ('provider', 'openai'),
    ),
)
def test_rerank_request_rejects_invalid_or_private_fields(field: str, value: object) -> None:
    payload = valid_rerank_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        OpenAIRerankRequestModel.model_validate(payload)


def test_rerank_request_rejects_more_than_256_documents() -> None:
    with pytest.raises(ValidationError):
        OpenAIRerankRequestModel.model_validate({'model': 'company-rerank-v1', 'query': 'q', 'documents': ['x'] * 257})


def test_rerank_response_is_stable_bounded_and_drops_unknown_meta() -> None:
    response = OpenAIRerankResponseModel.model_validate(
        {
            'id': 'rrk_01Habc',
            'results': [
                {
                    'index': 1,
                    'relevance_score': 0.9921,
                    'document': {'text': 'Refunds are accepted within thirty days.'},
                },
                {'index': 0, 'relevance_score': 0.11},
            ],
            'meta': {
                'billed_units': {'search_units': 1},
                'tokens': {'input_tokens': 22},
                'unknown_upstream_field': 'must-not-survive',
            },
            'binding_id': 'must-not-survive',
        }
    )

    serialized = response.model_dump()
    assert serialized['results'][0]['document'] == {'text': 'Refunds are accepted within thirty days.'}
    assert serialized['results'][1]['document'] is None
    assert serialized['meta'] == {'billed_units': {'search_units': 1}, 'tokens': {'input_tokens': 22}}
    assert 'unknown_upstream_field' not in serialized['meta']
    assert 'binding_id' not in serialized


def test_rerank_response_rejects_non_finite_scores() -> None:
    with pytest.raises(ValidationError):
        OpenAIRerankResponseModel.model_validate(
            {
                'id': 'rrk_01Habc',
                'results': [{'index': 0, 'relevance_score': float('inf')}],
                'meta': {},
            }
        )
