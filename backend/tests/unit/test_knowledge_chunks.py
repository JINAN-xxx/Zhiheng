import math
from uuid import uuid4

import pytest


def test_chunks_preserve_raw_text_and_model_text_normalization() -> None:
    from zhiheng.modules.knowledge.chunks import split_chunks, validate_vectors

    text = 'First paragraph.\n' * 50
    chunks = split_chunks(text, 100, 20)
    assert len(chunks) > 1 and chunks[0].content.startswith('First paragraph.\n')
    assert '\n' not in chunks[0].model_text
    assert all(len(chunk.content) <= 100 for chunk in chunks)
    assert validate_vectors({'data': [{'index': 0, 'embedding': [0.1, 0.2]}]}, 1) == [[0.1, 0.2]]
    for vector in ([0, 0], [math.nan, 1], [math.inf, 1], [1e100, 1.0], [1e-100, 0.0]):
        with pytest.raises(ValueError):
            validate_vectors({'data': [{'index': 0, 'embedding': vector}]}, 1)


def test_vector_rows_must_match_inputs_and_chunk_count_is_bounded() -> None:
    from zhiheng.modules.knowledge.chunks import split_chunks, validate_vectors

    with pytest.raises(ValueError):
        validate_vectors({'data': [{'index': 1, 'embedding': [0.1, 0.2]}]}, 1)
    with pytest.raises(ValueError):
        split_chunks('a' * 200_001, 100, 0)


def test_knowledge_text_contracts_reject_unencodable_or_empty_names() -> None:
    from zhiheng.modules.knowledge.public import BaseCreate, BasePatch, FolderCreate, QueryRequest

    cases = [
        (BaseCreate, {'name': '\ud800', 'embed_model_id': uuid4()}),
        (BasePatch, {'name': '  ', 'expected_version': 1}),
        (FolderCreate, {'name': '  '}),
        (QueryRequest, {'query': '\ud800', 'client_request_id': uuid4()}),
    ]
    for schema, payload in cases:
        with pytest.raises(ValueError):
            schema.model_validate(payload)


def test_prepared_context_retains_messages_and_images_with_empty_reference_data() -> None:
    from zhiheng.modules.knowledge.public import PreparedKnowledge
    from zhiheng.modules.models.public import ModelImage, ModelMessage

    image = ModelImage('data:image/jpeg;base64,aW1hZ2U=')
    original = (
        ModelMessage('system', 'Original system'),
        ModelMessage('developer', 'Original developer'),
        ModelMessage('user', 'Earlier question'),
        ModelMessage('assistant', 'Earlier answer'),
        ModelMessage('user', 'Current question', (image,)),
    )
    prepared = PreparedKnowledge(intent_id=uuid4(), bases=[], hits=[])
    messages = prepared.apply_context(original)
    assert messages[1:-1] == original[:-1]
    assert messages[-1].role == 'user' and messages[-1].images == (image,)
    assert messages[-1].content.endswith('Current question')
    assert '未找到相关资料' in messages[-1].content
    assert messages[0].role == 'system' and '资料不足' in messages[0].content
    assert original[-1].content == 'Current question'


@pytest.mark.parametrize('messages', [(), ('assistant',)])
def test_prepared_context_requires_current_user_message(messages: tuple[str, ...]) -> None:
    from zhiheng.modules.knowledge.public import PreparedKnowledge
    from zhiheng.modules.models.public import ModelMessage

    prepared = PreparedKnowledge(intent_id=uuid4(), bases=[], hits=[])
    with pytest.raises(ValueError, match='current user message'):
        prepared.apply_context(tuple(ModelMessage('assistant', value) for value in messages))
