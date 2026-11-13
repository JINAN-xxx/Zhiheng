"""Delete only one fully identified, already stopped and unpinned graph generation."""

from zhiheng.modules.knowledge.adapters.lightrag.postgres import PostgresContext
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.modules.knowledge.graph.contracts import GraphVectorManifest
from zhiheng.shared.errors import AppError

_TABLES = (
    'lightrag_graph_edges',
    'lightrag_graph_nodes',
    'lightrag_doc_full',
    'lightrag_doc_chunks',
    'lightrag_llm_cache',
    'lightrag_doc_status',
    'lightrag_full_entities',
    'lightrag_full_relations',
    'lightrag_entity_chunks',
    'lightrag_relation_chunks',
)


async def delete_generation(*, url: str, uri: str, token: str, database: str, manifest: GraphVectorManifest) -> None:
    rpc = GraphVectorRPC(uri=uri, token=token, database=database, control=VectorIOControl(timeout_seconds=60))
    context = PostgresContext(url=url, workspace=manifest.workspace, read_only=False, check=lambda: None)
    try:
        await context.open()
        for role in ('entities', 'relationships', 'chunks'):
            name = manifest.collection(role)
            if await rpc.acall('has_collection', name):
                actual = await rpc.acall('describe_collection', name)
                if actual.get('description') != manifest.description(role):
                    raise AppError('graph_identity_mismatch', '图谱索引身份不匹配，拒绝回收', 503)
                await rpc.acall('drop_collection', name, timeout=30)
        assert context.db.pool is not None
        async with context.db.pool.acquire() as connection, connection.transaction():
            for table in _TABLES:
                await connection.execute(f'DELETE FROM {table} WHERE workspace=$1', manifest.workspace)
    finally:
        await rpc.aclose()
        await context.close()
