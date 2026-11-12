from uuid import UUID, uuid4

import pytest

from zhiheng.modules.knowledge.contracts import Hit
from zhiheng.modules.knowledge.ranking import balance_candidates, rank_candidates
from zhiheng.shared.errors import AppError


def hit(base: UUID | None = None, score: float = 0.8, text: str = 'original') -> Hit:
    return Hit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_id=uuid4(),
        source_id=uuid4(),
        knowledge_base_id=base or uuid4(),
        index_revision=uuid4(),
        title='title',
        content=text,
        score=score,
    )


def test_multiple_models_are_balanced_without_comparing_scores() -> None:
    left, right = uuid4(), uuid4()
    groups = [[hit(left, 0.01) for _ in range(40)], [hit(right, 0.99) for _ in range(40)]]
    result = balance_candidates(groups, 8)
    assert [row.knowledge_base_id for row in result] == [left, right] * 4
    assert result[::2] == groups[0][:4]
    assert len(balance_candidates(groups * 3, 80)) <= 80


def test_ranker_maps_only_original_candidates_and_preserves_provider_order() -> None:
    original = [hit(text='first'), hit(text='second')]
    result = rank_candidates(
        original,
        {
            'results': [
                {'index': 1, 'relevance_score': 0.91, 'document': {'text': 'tampered'}},
                {'index': 0, 'relevance_score': 0.12},
            ]
        },
    )
    assert [row.content for row in result] == ['second', 'first']
    assert [row.chunk_id for row in result] == [original[1].chunk_id, original[0].chunk_id]
    assert result[0].rerank_score == 0.91
    assert result[0].score == original[1].score


@pytest.mark.parametrize(
    'rows',
    [
        [{'index': 2, 'relevance_score': 0.1}],
        [{'index': -1, 'relevance_score': 0.1}],
        [{'index': True, 'relevance_score': 0.1}],
        [{'index': 0, 'relevance_score': float('nan')}],
        [{'index': 0, 'relevance_score': 0.1}, {'index': 0, 'relevance_score': 0.2}],
    ],
)
def test_malformed_ranking_is_rejected(rows: list) -> None:
    with pytest.raises(AppError):
        rank_candidates([hit(), hit()], {'results': rows})


def test_provider_fallback_only_allows_temporary_rerank_failures() -> None:
    from zhiheng.application.execution.executor import retrieval_provider_error
    from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest, ProviderError
    from zhiheng.runtime.contracts import ClaimedRun

    binding = ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='mock', max_output_tokens=10, model_type='rerank'
    )
    claim = ClaimedRun(
        uuid4(),
        'knowledge_rerank',
        uuid4(),
        uuid4(),
        uuid4(),
        ModelRequest(binding=binding, messages=(ModelMessage('user', 'q'),)).model_dump(mode='json'),
    )
    assert (
        retrieval_provider_error(claim, ProviderError(reason='http_error', upstream_status=429)).code
        == 'knowledge_rerank_transient'
    )
    assert (
        retrieval_provider_error(claim, ProviderError(reason='http_error', upstream_status=503)).code
        == 'knowledge_rerank_transient'
    )
    assert retrieval_provider_error(claim, ProviderError(reason='timeout')).code == 'knowledge_rerank_transient'
    assert (
        retrieval_provider_error(claim, ProviderError(reason='http_error', upstream_status=403)).code
        != 'knowledge_rerank_transient'
    )
    assert (
        retrieval_provider_error(claim, ProviderError(reason='invalid_response')).code != 'knowledge_rerank_transient'
    )
    from dataclasses import replace

    assert (
        retrieval_provider_error(replace(claim, resource_type='public'), ProviderError(reason='timeout')).code
        != 'knowledge_rerank_transient'
    )


def test_explicit_upstream_denial_cannot_be_mislabeled_as_transport_fallback() -> None:
    from zhiheng.application.execution.executor import retrieval_provider_error
    from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest, ProviderError
    from zhiheng.runtime.contracts import ClaimedRun

    binding = ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='mock', max_output_tokens=10, model_type='rerank'
    )
    claim = ClaimedRun(
        uuid4(),
        'knowledge_graph',
        uuid4(),
        uuid4(),
        uuid4(),
        ModelRequest(binding=binding, messages=(ModelMessage('user', 'q'),)).model_dump(mode='json'),
    )
    for status in (401, 403):
        assert (
            retrieval_provider_error(claim, ProviderError(reason='transport_error', upstream_status=status)).code
            != 'knowledge_rerank_transient'
        )
