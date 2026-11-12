"""Every synchronous client is owned and closed by its drained worker thread."""

import asyncio
import json
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import UUID

from pymilvus import (  # type: ignore[import-untyped]
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    WeightedRanker,
)

from zhiheng.modules.knowledge.adapters.milvus_rpc import rpc_channel
from zhiheng.modules.knowledge.chunks import same_vector
from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.modules.knowledge.vector import VectorSpec
from zhiheng.shared.errors import AppError

T = TypeVar('T')
RPC_TIMEOUT = 8
CHINESE_ANALYZER = {'tokenizer': 'jieba', 'filter': ['lowercase']}


async def drain_thread(operation: Callable[[], T], *, on_cancel: Callable[[], None] | None = None) -> T:
    future = asyncio.get_running_loop().run_in_executor(None, operation)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        if on_cancel:
            on_cancel()
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:  # noqa: PERF203 - drain survives repeated cancellation
                continue
            except Exception:
                break
        if future.done() and not future.cancelled():
            future.exception()
        raise


class _CancellableClient:
    """Stop before the next RPC; the current blocking RPC must finish before close."""

    def __init__(self, client: Any, control: VectorIOControl) -> None:
        self._client, self._control = client, control

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._client, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self._control.method(float(kwargs.get('timeout', RPC_TIMEOUT)))
            # None activates the SDK's retry-count branch; the real transport
            # receives the absolute remaining timeout from our channel instead.
            kwargs.update(timeout=None, retry_times=0, retry_on_rate_limit=False)
            return method(*args, **kwargs)

        return call


class MilvusStore:
    def __init__(self, uri: str | None, token: str = '', database: str = 'default') -> None:
        self.uri, self.token, self.database = uri, token, database

    async def _call(
        self,
        operation: Callable[[Any], T],
        *,
        timeout_seconds: float | None = None,
        control: VectorIOControl | None = None,
    ) -> T:
        if not self.uri:
            raise AppError('knowledge_unavailable', '未配置Milvus索引服务', 503)
        control = control or VectorIOControl(timeout_seconds=timeout_seconds or 60)

        def invoke() -> T:
            assert self.uri is not None
            control.method(RPC_TIMEOUT)
            channel = rpc_channel(self.uri, self.token, self.database, control)
            try:
                client = MilvusClient(
                    uri=self.uri,
                    token=self.token,
                    db_name=self.database,
                    timeout=control.remaining(),
                    dedicated=True,
                    channel=channel,
                    grpc_options={'grpc.enable_retries': 0},
                )
                try:
                    return operation(_CancellableClient(client, control))
                finally:
                    client.close()
            finally:
                channel.close()

        try:
            return await drain_thread(invoke, on_cancel=control.cancel)
        except AppError:
            raise
        except Exception:
            raise AppError('knowledge_unavailable', '知识索引服务不可用', 503) from None

    async def available(self) -> bool:
        try:
            return bool(await self._call(lambda client: client.get_server_version(timeout=RPC_TIMEOUT)))
        except AppError:
            return False

    @staticmethod
    def _validate(client: Any, spec: VectorSpec, *, loaded: bool) -> None:
        description = client.describe_collection(spec.collection_name, timeout=RPC_TIMEOUT)
        if description.get('description') != spec.description() or description.get('enable_dynamic_field'):
            raise AppError('index_spec_mismatch', '索引规格不匹配', 503)
        fields = {field['name']: field for field in description['fields']}
        expected_fields = {'id', 'document_id', 'content_hash', 'vector'}
        if spec.hybrid:
            expected_fields |= {'text', 'sparse'}
        if set(fields) != expected_fields:
            raise AppError('index_spec_mismatch', '索引字段不匹配', 503)
        if (
            int(fields['vector']['type']) != int(DataType.FLOAT_VECTOR)
            or int(fields['vector']['params']['dim']) != spec.dimension
            or not fields['id'].get('is_primary')
            or any(int(fields[name]['type']) != int(DataType.VARCHAR) for name in ('id', 'document_id', 'content_hash'))
        ):
            raise AppError('index_spec_mismatch', '索引维度不匹配', 503)
        if spec.hybrid:
            params = fields['text'].get('params', {})
            analyzer = params.get('analyzer_params')
            if isinstance(analyzer, str):
                analyzer = json.loads(analyzer)
            functions = description.get('functions', [])
            if (
                int(fields['text']['type']) != int(DataType.VARCHAR)
                or not params.get('enable_analyzer')
                or analyzer != CHINESE_ANALYZER
                or int(fields['sparse']['type']) != int(DataType.SPARSE_FLOAT_VECTOR)
                or len(functions) != 1
                or int(functions[0]['type']) != int(FunctionType.BM25)
                or functions[0].get('input_field_names') != ['text']
                or functions[0].get('output_field_names') != ['sparse']
            ):
                raise AppError('index_spec_mismatch', '索引关键词能力不匹配', 503)
        if loaded:
            index = client.describe_index(spec.collection_name, 'vector_index', timeout=RPC_TIMEOUT)
            if index.get('index_type') != 'HNSW' or index.get('metric_type') != 'COSINE':
                raise AppError('index_spec_mismatch', '索引参数不匹配', 503)
            if spec.hybrid:
                sparse = client.describe_index(spec.collection_name, 'sparse_index', timeout=RPC_TIMEOUT)
                if sparse.get('index_type') != 'SPARSE_INVERTED_INDEX' or sparse.get('metric_type') != 'BM25':
                    raise AppError('index_spec_mismatch', '索引关键词参数不匹配', 503)
            state = client.get_load_state(spec.collection_name, timeout=RPC_TIMEOUT)
            if str(state.get('state')).split('.')[-1] != 'Loaded':
                raise AppError('index_not_loaded', '索引尚未加载', 503)

    async def build(
        self, spec: VectorSpec, rows: list[dict[str, object]], *, control: VectorIOControl | None = None
    ) -> None:
        def write(client: Any) -> None:
            if client.has_collection(spec.collection_name, timeout=RPC_TIMEOUT):
                self._validate(client, spec, loaded=False)
            else:
                schema = MilvusClient.create_schema(
                    auto_id=False, enable_dynamic_field=False, description=spec.description()
                )
                schema.add_field('id', DataType.VARCHAR, is_primary=True, max_length=36)
                schema.add_field('document_id', DataType.VARCHAR, max_length=36)
                schema.add_field('content_hash', DataType.VARCHAR, max_length=64)
                schema.add_field('vector', DataType.FLOAT_VECTOR, dim=spec.dimension)
                if spec.hybrid:
                    schema.add_field(
                        'text',
                        DataType.VARCHAR,
                        max_length=65535,
                        enable_analyzer=True,
                        analyzer_params=CHINESE_ANALYZER,
                    )
                    schema.add_field('sparse', DataType.SPARSE_FLOAT_VECTOR)
                    schema.add_function(
                        Function(
                            name='text_bm25',
                            input_field_names=['text'],
                            output_field_names=['sparse'],
                            function_type=FunctionType.BM25,
                        )
                    )
                client.create_collection(spec.collection_name, schema=schema, consistency_level='Strong', timeout=60)
            for offset in range(0, len(rows), 128):
                client.upsert(spec.collection_name, rows[offset : offset + 128], timeout=RPC_TIMEOUT)
            indexes = client.list_indexes(spec.collection_name, timeout=RPC_TIMEOUT)
            if 'vector_index' not in indexes:
                params = client.prepare_index_params()
                params.add_index(
                    field_name='vector',
                    index_name='vector_index',
                    index_type='HNSW',
                    metric_type='COSINE',
                    params={'M': 16, 'efConstruction': 256},
                )
                client.create_index(spec.collection_name, params, timeout=60)
            if spec.hybrid and 'sparse_index' not in indexes:
                params = client.prepare_index_params()
                params.add_index(
                    field_name='sparse',
                    index_name='sparse_index',
                    index_type='SPARSE_INVERTED_INDEX',
                    metric_type='BM25',
                    params={'inverted_index_algo': 'DAAT_MAXSCORE', 'bm25_k1': 1.2, 'bm25_b': 0.75},
                )
                client.create_index(spec.collection_name, params, timeout=60)
            client.load_collection(spec.collection_name, timeout=60)
            self._validate(client, spec, loaded=True)
            count = client.query(
                spec.collection_name,
                filter='',
                output_fields=['count(*)'],
                consistency_level='Strong',
                timeout=RPC_TIMEOUT,
            )
            if len(count) != 1 or count[0]['count(*)'] != len(rows):
                raise AppError('index_verification_failed', '索引条目验证失败', 503)
            for offset in range(0, len(rows), 128):
                expected = {str(row['id']): row for row in rows[offset : offset + 128]}
                actual = client.get(
                    spec.collection_name,
                    list(expected),
                    output_fields=['document_id', 'content_hash', 'vector'] + (['text'] if spec.hybrid else []),
                    consistency_level='Strong',
                    timeout=RPC_TIMEOUT,
                )
                if len(actual) != len(expected) or any(
                    str(row['id']) not in expected
                    or row['content_hash'] != expected[str(row['id'])]['content_hash']
                    or row['document_id'] != expected[str(row['id'])]['document_id']
                    or not same_vector(row.get('vector'), expected[str(row['id'])]['vector'])
                    or (spec.hybrid and row.get('text') != expected[str(row['id'])].get('text'))
                    for row in actual
                ):
                    raise AppError('index_verification_failed', '索引内容验证失败', 503)

        await self._call(write, control=control)

    async def search(
        self,
        spec: VectorSpec,
        vector: list[float],
        limit: int,
        document_ids: list[UUID],
        *,
        timeout_seconds: float = RPC_TIMEOUT,
    ) -> list[dict[str, Any]]:
        def read(client: Any) -> list[dict[str, Any]]:
            self._validate(client, spec, loaded=True)
            if not document_ids:
                return []
            result = client.search(
                spec.collection_name,
                [vector],
                anns_field='vector',
                filter='document_id in ' + json.dumps([str(value) for value in document_ids]),
                limit=limit,
                search_params={'metric_type': 'COSINE', 'params': {'ef': 64}},
                output_fields=['document_id', 'content_hash', 'vector'],
                consistency_level='Strong',
                timeout=RPC_TIMEOUT,
            )
            return list(result[0])

        return await self._call(read, timeout_seconds=timeout_seconds)

    async def hybrid_search(
        self,
        spec: VectorSpec,
        vector: list[float],
        query: str,
        limit: int,
        document_ids: list[UUID],
        *,
        timeout_seconds: float = RPC_TIMEOUT,
    ) -> list[dict[str, Any]]:
        if not spec.hybrid:
            raise AppError('knowledge_basic_retrieval', '该索引仅支持基础检索，请先完成索引升级', 409)

        def read(client: Any) -> list[dict[str, Any]]:
            self._validate(client, spec, loaded=True)
            if not document_ids:
                return []
            expression = 'document_id in ' + json.dumps([str(value) for value in document_ids])
            requests = [
                AnnSearchRequest(
                    data=[vector],
                    anns_field='vector',
                    param={'metric_type': 'COSINE', 'params': {'ef': 64}},
                    limit=40,
                    expr=expression,
                ),
                AnnSearchRequest(
                    data=[query],
                    anns_field='sparse',
                    param={'metric_type': 'BM25', 'params': {}},
                    limit=40,
                    expr=expression,
                ),
            ]
            result = client.hybrid_search(
                spec.collection_name,
                requests,
                ranker=WeightedRanker(0.7, 0.3),
                limit=min(40, limit),
                output_fields=['document_id', 'content_hash', 'vector', 'text'],
                consistency_level='Strong',
                timeout=RPC_TIMEOUT,
            )
            return list(result[0])

        return await self._call(read, timeout_seconds=timeout_seconds)

    async def drop(self, spec: VectorSpec) -> None:
        def remove(client: Any) -> None:
            if client.has_collection(spec.collection_name, timeout=RPC_TIMEOUT):
                self._validate(client, spec, loaded=False)
                client.drop_collection(spec.collection_name, timeout=60)

        await self._call(remove)
