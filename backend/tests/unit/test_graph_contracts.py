from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from zhiheng.modules.knowledge.graph.contracts import (
    GraphConfigPatch,
    GraphPreviewResult,
    GraphQueryRequest,
    GraphSubgraphRequest,
)
from zhiheng.modules.models.public import ModelBinding, ModelOptions, ModelRequest


def test_graph_requests_forbid_unknown_and_bound_paid_parameters() -> None:
    assert GraphConfigPatch(expected_version=0, llm_model_id=uuid4()).concurrency == 2
    for request in (
        {'client_request_id': uuid4(), 'query': 'q', 'mode': 'invalid'},
        {'client_request_id': uuid4(), 'query': 'q', 'top_k': 9},
        {'client_request_id': uuid4(), 'query': 'q', 'hidden': True},
    ):
        with pytest.raises(ValidationError):
            GraphQueryRequest.model_validate(request)
    with pytest.raises(ValidationError):
        GraphSubgraphRequest(max_depth=6)


async def test_preview_persists_bounded_graph_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from lightrag.types import KnowledgeGraph, KnowledgeGraphEdge, KnowledgeGraphNode

    from zhiheng.modules.knowledge.adapters.lightrag import factory

    nodes = [
        KnowledgeGraphNode(
            id=str(index),
            labels=[str(index)],
            properties={'entity_name': f'Node {index}', 'description': 'a' * 360},
        )
        for index in range(82)
    ]
    edges = [
        KnowledgeGraphEdge(id=str(index), type='edge', source='0', target='1', properties={'description': 'b' * 360})
        for index in range(161)
    ]
    edges.insert(0, KnowledgeGraphEdge(id='outside', type='edge', source='0', target='81', properties={}))
    storage = SimpleNamespace(
        get_knowledge_graph=AsyncMock(return_value=KnowledgeGraph(nodes=nodes, edges=edges, is_truncated=False)),
        get_all_nodes=AsyncMock(return_value=nodes),
        get_all_edges=AsyncMock(return_value=edges),
    )
    rag = SimpleNamespace(
        chunk_entity_relation_graph=storage,
        doc_status=SimpleNamespace(get_status_counts=AsyncMock(return_value={'processed': 1})),
    )
    frozen = SimpleNamespace(chunks=[SimpleNamespace(document_id=uuid4())])
    manifest = SimpleNamespace(attempt_id=uuid4(), revision=uuid4())
    monkeypatch.setattr(factory, 'insert_managed', AsyncMock())

    raw = await factory.run_operation(rag, 'preview', frozen, manifest, {})
    result = GraphPreviewResult.model_validate(raw)
    storage.get_knowledge_graph.assert_awaited_once_with(node_label='*', max_depth=2, max_nodes=80)
    assert (result.entities, result.relations, result.chunks) == (82, 162, 1)
    graph = result.graph
    assert graph is not None
    assert graph.revision == manifest.revision
    assert graph.is_truncated
    assert len(graph.nodes) == 80 and len(graph.edges) == 160
    assert graph.nodes[0].entity_name == 'Node 0'
    assert graph.nodes[0].description == 'a' * 320
    assert graph.edges[0].description == 'b' * 320
    assert {edge.target for edge in graph.edges} == {'1'}
    assert GraphPreviewResult.model_validate({key: value for key, value in raw.items() if key != 'graph'}).graph is None


def test_typed_generation_controls_survive_snapshot_and_forbid_embedding() -> None:
    options = ModelOptions(response_format='json_object', reasoning_effort='low')
    binding = ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='local', max_output_tokens=128, options=options
    )
    request = ModelRequest(binding=binding, messages=())
    assert ModelRequest.model_validate_json(request.model_dump_json()).binding.options == options
    with pytest.raises(ValidationError):
        ModelRequest(binding=binding.model_copy(update={'model_type': 'embedding'}), messages=())


def test_absent_generation_options_preserve_published_vector_binding_fingerprint() -> None:
    assert set(ModelOptions().model_dump()) == {
        'temperature',
        'top_p',
        'presence_penalty',
        'frequency_penalty',
        'stop',
        'user',
        'dimensions',
        'top_n',
        'return_documents',
    }
    assert ModelOptions(response_format='json_object').model_dump()['response_format'] == 'json_object'


@pytest.mark.parametrize('port', ['0', '-1', '65536'])
def test_explicit_graph_port_never_falls_back_to_default(port: str) -> None:
    from zhiheng.modules.knowledge.adapters.lightrag.postgres import PostgresContext
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError):
        PostgresContext(
            url=f'postgresql://runtime:local@localhost:{port}/test',
            workspace='g_scope',
            read_only=True,
            check=lambda: None,
        )


def test_native_graph_url_preserves_explicit_database_identity() -> None:
    from zhiheng.modules.knowledge.adapters.lightrag.postgres import PostgresContext

    context = PostgresContext(
        url='postgresql://runtime:local@localhost/named%20database',
        workspace='g_scope',
        read_only=True,
        check=lambda: None,
    )
    assert context.db.database == 'named database' and context.db.port == 5432
