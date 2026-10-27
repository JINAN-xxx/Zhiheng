"""Manifest-bound Milvus collections: explicit provision, read-only validation and exact GC."""

import hashlib
import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from uuid import UUID

from pymilvus import CollectionSchema, MilvusClient  # type: ignore[import-untyped]

from lightrag.base import BaseVectorStorage  # type: ignore[import-untyped]
from lightrag.kg.milvus_impl import MilvusIndexConfig, MilvusVectorDBStorage  # type: ignore[import-untyped]
from lightrag.kg.shared_storage import get_namespace_lock  # type: ignore[import-untyped]
from zhiheng.modules.knowledge.adapters.lightrag.postgres import CURRENT_STORAGE, PostgresContext
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
from zhiheng.shared.errors import AppError

VectorRole = Literal['entities', 'relationships', 'chunks']
MAX_GRAPH_DIMENSION = 65536


@dataclass(frozen=True)
class GraphVectorManifest:
    base_id: UUID
    revision: UUID
    attempt_id: UUID
    fingerprint: str
    dimension: int
    workspace: str

    def __post_init__(self) -> None:
        if (
            self.workspace != 'g_' + self.attempt_id.hex
            or not re.fullmatch('[a-f0-9]{64}', self.fingerprint)
            or not 1 <= self.dimension <= MAX_GRAPH_DIMENSION
        ):
            raise AppError('graph_manifest_invalid', '图谱版本完整身份无效', 503)

    def dump(self) -> dict[str, object]:
        return {
            'base_id': str(self.base_id),
            'revision': str(self.revision),
            'attempt_id': str(self.attempt_id),
            'fingerprint': self.fingerprint,
            'dimension': self.dimension,
            'workspace': self.workspace,
        }

    @classmethod
    def load(cls, value: dict[str, object]) -> 'GraphVectorManifest':
        dimension = value['dimension']
        if type(dimension) is not int:
            raise AppError('graph_manifest_invalid', '图谱版本身份无效', 503)
        return cls(
            UUID(str(value['base_id'])),
            UUID(str(value['revision'])),
            UUID(str(value['attempt_id'])),
            str(value['fingerprint']),
            dimension,
            str(value['workspace']),
        )

    def collection(self, role: VectorRole) -> str:
        return 'zh_g_' + hashlib.sha256((self.description(role)).encode()).hexdigest()

    def description(self, role: VectorRole) -> str:
        return json.dumps(
            {
                'sdk': '1.5.7',
                'base': str(self.base_id),
                'revision': str(self.revision),
                'attempt': str(self.attempt_id),
                'fingerprint': self.fingerprint,
                'dimension': self.dimension,
                'workspace': self.workspace,
                'role': role,
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class VectorContext:
    uri: str
    token: str
    database: str
    manifest: GraphVectorManifest
    control: VectorIOControl
    provision: bool
    rpcs: list[GraphVectorRPC] = field(default_factory=list)


CURRENT_VECTOR: ContextVar[VectorContext] = ContextVar('graph_vector_context')


class _SDKVectorClient:
    def __init__(self, rpc: GraphVectorRPC, *, owner: PostgresContext) -> None:
        self.rpc, self.owner, self.read_only = rpc, owner, owner.read_only

    def __getattr__(self, name: str) -> Any:
        if name == 'close':
            return self.rpc.close
        if name == 'load_collection':

            def validate_loaded(collection_name: str, **kwargs: object) -> None:
                self.owner.check()
                state = self.rpc.call('get_load_state', collection_name)
                if str(state.get('state')).split('.')[-1] != 'Loaded':
                    raise AppError('graph_index_unloaded', '图谱索引未加载', 503)

            return validate_loaded
        if name in {'create_collection', 'create_index', 'drop_collection'}:
            raise AppError('graph_provision_denied', 'SDK不能创建或修复图谱资源', 409)
        if self.read_only and name not in {
            'query',
            'search',
            'get',
            'has_collection',
            'describe_collection',
            'list_indexes',
            'describe_index',
            'get_load_state',
            'get_server_version',
        }:
            raise AppError('graph_read_only', '活动图谱禁止写入', 409)

        def guarded(*args: Any, **kwargs: Any) -> Any:
            if name in {'upsert', 'insert', 'delete', 'flush'}:
                self.owner.write()
            else:
                self.owner.check()
            return self.rpc.call(name, *args, **kwargs)

        return guarded


@dataclass
class GraphVectorStorage(BaseVectorStorage):  # type: ignore[misc]
    def __post_init__(self) -> None:
        self.context = CURRENT_VECTOR.get()
        self.owner = CURRENT_STORAGE.get()
        roles = [role for role in ('entities', 'relationships', 'chunks') if self.namespace.endswith(role)]
        if len(roles) != 1:
            raise AppError('graph_namespace_mismatch', '图谱向量角色无效', 503)
        self.role = cast('VectorRole', roles[0])
        self.collection = self.context.manifest.collection(self.role)
        self.delegate = MilvusVectorDBStorage(
            self.namespace,
            self.workspace,
            self.global_config,
            self.embedding_func,
            meta_fields=self.meta_fields,
        )
        # Constructor reads ambient workspace. Replace derived values before any
        # lifecycle/network operation; no global environment mutation is needed.
        self.delegate.workspace = self.workspace
        self.delegate.final_namespace = self.collection
        self.delegate.legacy_namespace = self.collection
        self.delegate._flush_lock = get_namespace_lock(namespace=self.collection, workspace='')
        self.delegate.index_config = MilvusIndexConfig(
            index_type='HNSW',
            metric_type='COSINE',
            hnsw_m=16,
            hnsw_ef_construction=256,
            hnsw_ef=64,
            sq_type='SQ8',
            sq_refine=False,
            sq_refine_type='FP32',
            sq_refine_k=10,
            ivf_nlist=1024,
            ivf_nprobe=16,
        )
        self.delegate._max_upsert_payload_bytes = 2_000_000
        self.delegate._max_upsert_records_per_batch = 128
        self.delegate._max_delete_records_per_batch = 128
        self.rpc = GraphVectorRPC(
            uri=self.context.uri,
            token=self.context.token,
            database=self.context.database,
            control=self.context.control,
        )
        # The LightRAG constructor may fail before returning its rag object.
        # Register the actual resource before its first lifecycle/network use.
        self.context.rpcs.append(self.rpc)
        self.error: BaseException | None = None

    def _schema(self) -> Any:
        schema = self.delegate._create_schema_for_namespace()
        return CollectionSchema(
            fields=schema.fields,
            description=self.context.manifest.description(self.role),
            enable_dynamic_field=True,
        )

    async def initialize(self) -> None:
        name = self.collection
        try:
            exists = await self.rpc.acall('has_collection', name)
            if not exists:
                if not self.context.provision or self.owner.read_only:
                    raise AppError('graph_collection_missing', '图谱索引资源缺失', 503)
                schema = self._schema()
                self.owner.write()
                await self.rpc.acall('create_collection', name, schema=schema, consistency_level='Strong', timeout=60)
                params = self.delegate.index_config
                indexes = MilvusClient.prepare_index_params()
                params.build_index_params(indexes)
                self.owner.write()
                await self.rpc.acall('create_index', name, indexes, timeout=60)
                self.owner.write()
                await self.rpc.acall('load_collection', name, timeout=60)
            await self.validate()
            self.delegate._client = _SDKVectorClient(self.rpc, owner=self.owner)
            self.delegate._initialized = True
        except BaseException:
            await self.rpc.aclose()
            raise

    async def validate(self) -> None:
        actual = await self.rpc.acall('describe_collection', self.collection)
        expected = self._schema().to_dict()
        if actual.get('description') != self.context.manifest.description(self.role):
            raise AppError('graph_identity_mismatch', '图谱索引身份不匹配', 503)
        fields = actual.get('fields', [])
        wanted = expected['fields']

        def normalize(rows: list[dict[str, Any]]) -> dict[str, object]:
            return {
                row['name']: {
                    'type': int(row['type']),
                    'params': row.get('params', {}),
                    'is_primary': bool(row.get('is_primary', False)),
                    'nullable': bool(row.get('nullable', False)),
                    'is_partition_key': bool(row.get('is_partition_key', False)),
                    'is_clustering_key': bool(row.get('is_clustering_key', False)),
                    'default_value': row.get('default_value'),
                }
                for row in rows
            }

        if normalize(fields) != normalize(wanted) or not actual.get('enable_dynamic_field') or actual.get('auto_id'):
            raise AppError('graph_index_incompatible', '图谱索引字段不兼容', 503)
        names = await self.rpc.acall('list_indexes', self.collection)
        if names != ['vector']:
            raise AppError('graph_index_incompatible', '图谱索引缺失或不兼容', 503)
        index = await self.rpc.acall('describe_index', self.collection, 'vector')
        if (
            index.get('index_type') != 'HNSW'
            or index.get('metric_type') != 'COSINE'
            or index.get('field_name') != 'vector'
            or str(index.get('M')) != '16'
            or str(index.get('efConstruction')) != '256'
        ):
            raise AppError('graph_index_incompatible', '图谱索引参数不兼容', 503)
        state = await self.rpc.acall('get_load_state', self.collection)
        if str(state.get('state')).split('.')[-1] != 'Loaded':
            raise AppError('graph_index_unloaded', '图谱索引未加载', 503)

    async def index_done_callback(self) -> None:
        if self.owner.read_only:
            if self.delegate._pending_vector_docs or self.delegate._pending_vector_deletes:
                raise AppError('graph_read_only', '活动图谱出现待写数据', 409)
            return
        self.owner.write()
        await self.delegate.index_done_callback()

    async def drop_pending_index_ops(self) -> None:
        await self.delegate.drop_pending_index_ops()

    async def finalize(self) -> None:
        try:
            await self.index_done_callback()
        except BaseException as error:
            self.error = error
            raise
        finally:
            await self.rpc.aclose()

    async def drop(self) -> dict[str, str]:
        raise AppError('graph_drop_denied', '图谱只能按受管版本回收', 409)

    async def query(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.query(*args, **kwargs)

    async def get_by_id(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_id(*args, **kwargs)

    async def get_by_ids(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_ids(*args, **kwargs)

    async def get_vectors_by_ids(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_vectors_by_ids(*args, **kwargs)

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.upsert(*args, **kwargs)

    async def delete_entity(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.delete_entity(*args, **kwargs)

    async def delete_entity_relation(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.delete_entity_relation(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.delete(*args, **kwargs)
