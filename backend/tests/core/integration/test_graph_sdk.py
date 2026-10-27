import asyncio
import hashlib
import os
import re
import secrets
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from tests.core.resources import resource
from zhiheng.bootstrap.migrations import migrate
from zhiheng.modules.knowledge.adapters.lightrag.sdk import GraphSDK, SDKCall
from zhiheng.modules.knowledge.adapters.lightrag.vector import GraphVectorManifest
from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
from zhiheng.modules.knowledge.graph.contracts import GraphChunk, GraphConfig, GraphFrozen, sdk_chunk_id
from zhiheng.modules.models.public import ModelBinding
from zhiheng.shared.errors import AppError


@pytest.mark.parametrize('concurrency', [1, 4])
async def test_real_sdk_registry_managed_provenance_and_three_collections(
    settings: object, tmp_path: object, monkeypatch: pytest.MonkeyPatch, concurrency: int
) -> None:
    from pymilvus import MilvusClient

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    await migrate(settings)
    url = make_url(settings.database_url.get_secret_value().replace('postgresql+asyncpg:', 'postgresql:'))
    admin = await asyncpg.connect(url.render_as_string(hide_password=False))
    role = 'graph_sdk_' + uuid4().hex
    password = secrets.token_urlsafe(24)
    runtime = url.set(username=role, password=password).render_as_string(hide_password=False)
    base, attempt, revision = uuid4(), uuid4(), uuid4()
    manifest = GraphVectorManifest(base, revision, attempt, 'a' * 64, 2, 'g_' + attempt.hex)
    client = MilvusClient(uri=uri, timeout=5)
    resource('intent', 'postgres_role', role, database=url.database)
    collections = {
        manifest.collection(role): manifest.description(role) for role in ('entities', 'relationships', 'chunks')
    }
    sdk = None
    try:
        await admin.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
        resource('created', 'postgres_role', role, database=url.database)
        await admin.execute(f'REVOKE CREATE,TEMPORARY ON DATABASE "{url.database}" FROM PUBLIC')
        await admin.execute(f'GRANT USAGE ON SCHEMA knowledge_graph_sdk TO "{role}"')
        await admin.execute(
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"'
        )
        for name, description in collections.items():
            assert not client.has_collection(name)
            resource('intent', 'milvus_collection', name, description=description, database='default')
        chunks = []
        index_revision = uuid4()
        for _ in range(2):
            document = uuid4()
            file_id = uuid4()
            document_revision = uuid4()
            for ordinal in range(4):
                chunk = uuid4()
                chunks.append(
                    GraphChunk(
                        id=chunk,
                        document_id=document,
                        file_id=file_id,
                        index_revision=index_revision,
                        document_revision=document_revision,
                        ordinal=ordinal,
                        content=f'甲机构和乙公司开展合作研究。样本{ordinal}',
                        content_hash=hashlib.sha256(f'甲机构和乙公司开展合作研究。样本{ordinal}'.encode()).hexdigest(),
                        sdk_id=sdk_chunk_id(document, chunk),
                    )
                )
        binding = ModelBinding(
            model_id=uuid4(), connection_id=uuid4(), upstream_model='same_model', max_output_tokens=4096
        )
        frozen = GraphFrozen(
            base_id=base,
            base_owner_id=UUID(int=0),
            content_fingerprint='a' * 64,
            config=GraphConfig(
                version=1, llm_model_id=binding.model_id, max_output_tokens=4096, concurrency=concurrency
            ),
            llm_binding=binding.model_dump(mode='json'),
            embedding_binding=binding.model_copy(update={'model_type': 'embedding'}).model_dump(mode='json'),
            dimension=2,
            index_revision=index_revision,
            base_version=1,
            base_acl_version=1,
            sources=[],
            chunks=chunks,
            fingerprint='a' * 64,
        )
        monkeypatch.setenv('MILVUS_WORKSPACE', 'conflicting_workspace')
        monkeypatch.setenv('POSTGRES_WORKSPACE', 'conflicting_workspace')
        for key in ('POSTGRES_HOST', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DATABASE'):
            monkeypatch.setenv(key, 'conflicting-explicit-context')
        monkeypatch.setenv('POSTGRES_PORT', '1')
        monkeypatch.setenv('MILVUS_URI', 'http://127.0.0.1:1')
        monkeypatch.setenv('MILVUS_TOKEN', 'conflicting-token')
        monkeypatch.setenv('MILVUS_DB_NAME', 'conflicting-database')
        calls = []
        parallel_active = 0
        parallel_max = 0
        summaries = 0

        async def model(call: SDKCall) -> object:
            nonlocal parallel_active, parallel_max, summaries
            calls.append(call)
            parallel_active += 1
            parallel_max = max(parallel_max, parallel_active)
            try:
                await asyncio.sleep(0.01)
            finally:
                parallel_active -= 1
            if call.role == 'embedding':
                return [[0.1, 0.2] for _ in call.messages]
            if call.json_object:
                return '{"high_level_keywords":["合作"],"low_level_keywords":["甲机构"]}'
            text = '\n'.join(message.content for message in call.messages)
            if 'synthesize a list of descriptions' in text:
                summaries += 1
                return '双方合作研究。'
            if 'Knowledge Graph Specialist' in text:
                matches = re.findall('样本([0-3])', text)
                sample = matches[-1] if matches else '0'
                return f'entity<|#|>甲机构<|#|>Organization<|#|>甲机构参与样本{sample}研究。\nentity<|#|>乙公司<|#|>Organization<|#|>乙公司参与样本{sample}研究。\nrelation<|#|>甲机构<|#|>乙公司<|#|>合作<|#|>双方样本{sample}合作。\n<|COMPLETE|>'
            if 'summar' in text.lower():
                summaries += 1
                return '双方合作研究。'
            return '<|COMPLETE|>'

        sdk = GraphSDK(
            database_url=runtime, milvus_uri=uri, milvus_token='', milvus_database='default', working_root=tmp_path
        )
        outcome = await sdk.execute(
            operation='build',
            frozen=frozen,
            manifest=manifest,
            request={},
            model=model,
            control=VectorIOControl(timeout_seconds=60),
        )
        assert outcome.stopped and outcome.result['entities'] == 2 and outcome.result['relations'] == 1
        async with admin.transaction():
            rows = await admin.fetch(
                'SELECT id,full_doc_id,file_path FROM knowledge_graph_sdk.lightrag_doc_chunks WHERE workspace=$1',
                manifest.workspace,
            )
            assert {row['id'] for row in rows} == {chunk.sdk_id for chunk in chunks}
            assert len({row['full_doc_id'] for row in rows}) == 2
            assert all(row['file_path'].startswith('managed/') for row in rows)
        assert any(call.role == 'extract' for call in calls) and any(call.role == 'embedding' for call in calls)
        assert summaries > 0
        assert parallel_max >= 2 if concurrency == 4 else parallel_max == 1
        if concurrency == 1:
            call_count = len(calls)
            name = manifest.collection('entities')
            client.release_collection(name)
            client.drop_index(name, 'vector')
            with pytest.raises(AppError):
                await sdk.execute(
                    operation='labels',
                    frozen=frozen,
                    manifest=manifest,
                    request={},
                    model=model,
                    control=VectorIOControl(timeout_seconds=20),
                )
            assert client.list_indexes(name) == [] and len(calls) == call_count
            indexes = MilvusClient.prepare_index_params()
            indexes.add_index(
                field_name='vector',
                index_name='vector',
                index_type='HNSW',
                metric_type='COSINE',
                params={'M': 16, 'efConstruction': 256},
            )
            client.create_index(name, indexes)
            client.load_collection(name)
            readable = await sdk.execute(
                operation='labels',
                frozen=frozen,
                manifest=manifest,
                request={},
                model=model,
                control=VectorIOControl(timeout_seconds=20),
            )
            assert set(readable.result['items']) == {'甲机构', '乙公司'} and len(calls) == call_count
        if concurrency == 4:
            entered = asyncio.Event()
            release = asyncio.Event()
            other_manifests = []
            for _ in range(2):
                other_attempt = uuid4()
                other = GraphVectorManifest(base, uuid4(), other_attempt, 'a' * 64, 2, 'g_' + other_attempt.hex)
                other_manifests.append(other)
                for vector_role in ('entities', 'relationships', 'chunks'):
                    name = other.collection(vector_role)
                    description = other.description(vector_role)
                    assert not client.has_collection(name)
                    collections[name] = description
                    resource('intent', 'milvus_collection', name, description=description, database='default')

            async def blocked(call: SDKCall) -> object:
                entered.set()
                await release.wait()
                return await model(call)

            cancelled = asyncio.create_task(
                sdk.execute(
                    operation='build',
                    frozen=frozen,
                    manifest=other_manifests[0],
                    request={},
                    model=blocked,
                    control=VectorIOControl(timeout_seconds=60),
                )
            )
            neighbor = asyncio.create_task(
                sdk.execute(
                    operation='build',
                    frozen=frozen,
                    manifest=other_manifests[1],
                    request={},
                    model=model,
                    control=VectorIOControl(timeout_seconds=60),
                )
            )
            assert await asyncio.wait_for(entered.wait(), 20)
            cancelled.cancel()
            try:
                done, _ = await asyncio.wait({cancelled}, timeout=3)
                assert cancelled in done, 'Cancelled attempt failed to drain in bounded probe'
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await cancelled
            outcome2 = await asyncio.wait_for(neighbor, 60)
            assert outcome2.stopped and outcome2.result['entities'] == 2
            assert (
                await admin.fetchval(
                    'SELECT count(*) FROM knowledge_graph_sdk.lightrag_graph_nodes WHERE workspace=$1',
                    other_manifests[1].workspace,
                )
                == 2
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM knowledge_graph_sdk.lightrag_doc_status WHERE workspace=$1 AND status='processed'",
                    other_manifests[0].workspace,
                )
                == 0
            )

    finally:
        if sdk is not None:
            await sdk.close()
        for name, description in collections.items():
            if client.has_collection(name):
                assert client.describe_collection(name)['description'] == description
                client.drop_collection(name)
            assert not client.has_collection(name)
            resource('deleted', 'milvus_collection', name, database='default')
        client.close()
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        resource('deleted', 'postgres_role', role, database=url.database)
        await admin.close()
