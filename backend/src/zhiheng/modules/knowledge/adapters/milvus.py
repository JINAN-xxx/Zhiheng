"""Every synchronous client is owned and closed by its drained worker thread."""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from pymilvus import DataType, MilvusClient  # type: ignore[import-untyped]

from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl, rpc_channel
from zhiheng.modules.knowledge.chunks import same_vector
from zhiheng.shared.errors import AppError

T = TypeVar('T')
RPC_TIMEOUT = 8


@dataclass(frozen=True)
class VectorSpec:
    base_id: UUID
    revision: UUID
    attempt_id: UUID
    collection_name: str
    fingerprint: str
    dimension: int | None

    def description(self) -> str:
        expected = f'zh_k_{self.base_id.hex}_{self.revision.hex}_{self.attempt_id.hex}'
        if (
            self.collection_name != expected
            or not re.fullmatch('[a-f0-9]{64}', self.fingerprint)
            or self.dimension is None
        ):
            raise AppError('invalid_index_identity', '索引身份无效', 503)
        return json.dumps(
            {
                'base': str(self.base_id),
                'revision': str(self.revision),
                'attempt': str(self.attempt_id),
                'fingerprint': self.fingerprint,
                'dimension': self.dimension,
            },
            sort_keys=True,
        )


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
        if set(fields) != {'id', 'document_id', 'content_hash', 'vector'}:
            raise AppError('index_spec_mismatch', '索引字段不匹配', 503)
        if (
            int(fields['vector']['type']) != int(DataType.FLOAT_VECTOR)
            or int(fields['vector']['params']['dim']) != spec.dimension
            or not fields['id'].get('is_primary')
            or any(int(fields[name]['type']) != int(DataType.VARCHAR) for name in ('id', 'document_id', 'content_hash'))
        ):
            raise AppError('index_spec_mismatch', '索引维度不匹配', 503)
        if loaded:
            index = client.describe_index(spec.collection_name, 'vector_index', timeout=RPC_TIMEOUT)
            if index.get('index_type') != 'HNSW' or index.get('metric_type') != 'COSINE':
                raise AppError('index_spec_mismatch', '索引参数不匹配', 503)
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
                    output_fields=['document_id', 'content_hash', 'vector'],
                    consistency_level='Strong',
                    timeout=RPC_TIMEOUT,
                )
                if len(actual) != len(expected) or any(
                    str(row['id']) not in expected
                    or row['content_hash'] != expected[str(row['id'])]['content_hash']
                    or row['document_id'] != expected[str(row['id'])]['document_id']
                    or not same_vector(row.get('vector'), expected[str(row['id'])]['vector'])
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

    async def drop(self, spec: VectorSpec) -> None:
        def remove(client: Any) -> None:
            if client.has_collection(spec.collection_name, timeout=RPC_TIMEOUT):
                self._validate(client, spec, loaded=False)
                client.drop_collection(spec.collection_name, timeout=60)

        await self._call(remove)
