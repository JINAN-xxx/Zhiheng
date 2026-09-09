# ruff: noqa: PLC0415
# Optional storage dependencies are deliberately imported at first use.
"""Authenticated platform boundary around the selected knowledge algorithms."""

import asyncio
import hashlib
import json
import math
import re
import shutil
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from module_Knowledge.src.knowledge.indexing import SUPPORTED_EXTENSIONS, process_file_to_markdown
from module_Knowledge.src.knowledge.kb_utils import normalize_embedding_text, split_text_into_chunks
from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.knowledge.models import ChunkParameters, DatabaseCreate, KnowledgeError, KnowledgeHit, QueryParameters

ModelFactory = Callable[[], AbstractAsyncContextManager[Any]]
MAX_FILENAME_LENGTH = 200
MAX_BINDINGS = 20
MAX_CONTEXT_BYTES = 24000
MAX_PASSAGES = 8

_RESOURCE_ID = re.compile(r'^(kb|doc|upl|folder)_[0-9a-f]{32}$')
_DB_FIELDS = (
    'db_id',
    'name',
    'description',
    'kb_type',
    'enable_knowledge_graph',
    'embed_model_id',
    'llm_model_id',
    'created_at',
    'chunk_size',
    'chunk_overlap',
)
_DOC_FIELDS = (
    'id',
    'file_name',
    'type',
    'status',
    'created_at',
    'file_size',
    'folder_id',
    'chunk_size',
    'chunk_overlap',
    'error',
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex}'


def _check_id(value: str, prefix: str) -> str:
    if not isinstance(value, str) or not _RESOURCE_ID.fullmatch(value) or not value.startswith(prefix + '_'):
        raise KnowledgeError.missing()
    return value


class KnowledgeRuntime:
    def __init__(
        self,
        data_dir: Path,
        model_service_factory: ModelFactory,
        owner_authorizer: Callable[[int], Awaitable[bool]],
        *,
        store_factory: Callable[[str], Any] | None = None,
        max_upload_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.model_service_factory = model_service_factory
        self.owner_authorizer = owner_authorizer
        self.max_upload_bytes = max_upload_bytes
        self.metadata = MetadataStore(self.data_dir)
        self._store_factory = store_factory
        self._stores: dict[str, Any] = {}
        self._graphs: dict[str, Any] = {}

    def _store(self, kind: str) -> Any:
        if kind not in self._stores:
            if self._store_factory:
                self._stores[kind] = self._store_factory(kind)
            else:
                from plugins.ai.knowledge.stores import create_store

                self._stores[kind] = create_store(kind, self.data_dir)
        return self._stores[kind]

    async def types(self) -> dict:
        from plugins.ai.knowledge.stores import available_types

        return {'kb_types': available_types()}

    async def close(self) -> None:
        for store in (*self._stores.values(), *self._graphs.values()):
            await store.close()
        self._stores.clear()
        self._graphs.clear()

    async def aclose(self) -> None:
        await self.close()

    @staticmethod
    def _database(state: dict, db_id: str) -> dict:
        _check_id(db_id, 'kb')
        database = state['databases'].get(db_id)
        if database is None:
            raise KnowledgeError.missing()
        return database

    @staticmethod
    def _document(state: dict, db_id: str, doc_id: str) -> dict:
        _check_id(doc_id, 'doc')
        document = state['documents'].get(doc_id)
        if not document or document.get('db_id') != db_id:
            raise KnowledgeError.missing()
        return document

    @staticmethod
    def _folder(state: dict, db_id: str, folder_id: str | None) -> dict | None:
        if folder_id is None:
            return None
        _check_id(folder_id, 'folder')
        folder = state['folders'].get(folder_id)
        if not folder or folder['db_id'] != db_id:
            raise KnowledgeError.missing()
        return folder

    @staticmethod
    def _safe_database(database: dict) -> dict:
        return {key: database.get(key) for key in _DB_FIELDS}

    async def list_databases(self) -> dict:
        state = await self.metadata.read()
        return {'databases': [self._safe_database(database) for database in state['databases'].values()]}

    async def get_database(self, db_id: str) -> dict:
        return self._safe_database(self._database(await self.metadata.read(), db_id))

    @staticmethod
    def _fingerprint(resolved: Any) -> str:
        parts = [
            resolved.platform_model_id,
            resolved.binding_id,
            resolved.engine_model_snapshot,
            (resolved.default_parameters or {}).get('dimensions'),
        ]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    async def _resolve(self, model_id: str, kind: str) -> Any:
        try:
            async with self.model_service_factory() as model_service:
                return await asyncio.wait_for(model_service.resolve_model(model_id, expected_type=kind), timeout=8)
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def create_database(self, payload: DatabaseCreate) -> dict:
        self._store(payload.kb_type)
        if payload.enable_knowledge_graph:
            from plugins.ai.knowledge.stores import require_graph

            require_graph()
            await self._resolve(payload.llm_model_id, 'chat')
        await self._resolve(payload.embed_model_id, 'embedding')
        database = {
            'db_id': _id('kb'),
            'name': payload.database_name,
            'description': payload.description,
            'kb_type': payload.kb_type,
            'enable_knowledge_graph': payload.enable_knowledge_graph,
            'embed_model_id': payload.embed_model_id,
            'llm_model_id': payload.llm_model_id,
            'created_at': _now(),
            'chunk_size': payload.chunk_size,
            'chunk_overlap': payload.chunk_overlap,
            'index_spec': None,
        }
        async with self.metadata.transaction() as state:
            state['databases'][database['db_id']] = database
        return self._safe_database(database)

    async def update_database(self, db_id: str, name: str, description: str) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id), self.metadata.transaction() as state:
            database = self._database(state, db_id)
            database.update(name=name, description=description)
            return self._safe_database(database)

    async def delete_database(self, db_id: str) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            database = self._database(state, db_id)
            try:
                await self._store(database['kb_type']).delete_database(db_id)
                if database['enable_knowledge_graph']:
                    graph_documents = [
                        document['id']
                        for document in state['documents'].values()
                        if document['db_id'] == db_id and document.get('graph_attempted')
                    ]
                    if graph_documents:
                        await (await self._graph(database)).delete_database(graph_documents)
            except Exception:
                raise KnowledgeError.unavailable() from None
            async with self.metadata.transaction() as state:
                state['databases'].pop(db_id)
                for collection in ('documents', 'uploads', 'folders'):
                    state[collection] = {key: item for key, item in state[collection].items() if item['db_id'] != db_id}
            upload_directory = self.data_dir / 'uploads' / db_id
            if upload_directory.exists():
                await asyncio.to_thread(shutil.rmtree, upload_directory)
        return {'deleted': True}

    @staticmethod
    def _filename(filename: str) -> tuple[str, str]:
        if (
            not filename
            or len(filename) > MAX_FILENAME_LENGTH
            or '/' in filename
            or '\\' in filename
            or any(unicodedata.category(character).startswith('C') for character in filename)
            or filename in {'.', '..'}
        ):
            raise KnowledgeError('KNOWLEDGE_FILE_INVALID', '文件名无效')
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise KnowledgeError('KNOWLEDGE_FILE_UNSUPPORTED', '不支持的文件类型')
        return filename, extension

    def _upload_path(self, upload: dict) -> Path:
        db_id = _check_id(upload['db_id'], 'kb')
        upload_id = _check_id(upload['upload_id'], 'upl')
        extension = upload['extension']
        if extension not in SUPPORTED_EXTENSIONS:
            raise KnowledgeError.missing()
        path = self.data_dir / 'uploads' / db_id / f'{upload_id}{extension}'
        if not path.resolve().is_relative_to(self.data_dir / 'uploads' / db_id) or path.is_symlink():
            raise KnowledgeError.missing()
        return path

    async def upload(
        self, db_id: str, filename: str, chunks: AsyncIterator[bytes], folder_id: str | None = None
    ) -> dict:
        _check_id(db_id, 'kb')
        filename, extension = self._filename(filename)
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            self._database(state, db_id)
            self._folder(state, db_id, folder_id)
            upload = {
                'upload_id': _id('upl'),
                'db_id': db_id,
                'file_name': filename,
                'extension': extension,
                'folder_id': folder_id,
                'file_size': 0,
                'created_at': _now(),
            }
            path = self._upload_path(upload)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as output:
                    async for chunk in chunks:
                        upload['file_size'] += len(chunk)
                        if upload['file_size'] > self.max_upload_bytes:
                            raise KnowledgeError('KNOWLEDGE_FILE_TOO_LARGE', '文件大小超过限制', 413)
                        await asyncio.to_thread(output.write, chunk)
                if upload['file_size'] == 0:
                    raise KnowledgeError('KNOWLEDGE_FILE_INVALID', '文件不能为空')
                async with self.metadata.transaction() as state:
                    state['uploads'][upload['upload_id']] = upload
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return {key: upload[key] for key in ('upload_id', 'file_name', 'file_size')}

    async def list_documents(self, db_id: str) -> dict:
        state = await self.metadata.read()
        self._database(state, db_id)
        documents = [
            {key: document.get(key) for key in _DOC_FIELDS}
            for document in state['documents'].values()
            if document['db_id'] == db_id
        ]
        return {'documents': documents, 'total': len(documents)}

    async def _embed(self, database: dict, texts: Sequence[str], resolved: Any) -> list[list[float]]:
        try:
            texts = [normalize_embedding_text(text) for text in texts]
        except ValueError:
            raise KnowledgeError('KNOWLEDGE_TEXT_INVALID', '文档包含不支持的控制字符') from None
        vectors = []
        try:
            async with self.model_service_factory() as models:
                for offset in range(0, len(texts), 32):
                    batch = texts[offset : offset + 32]
                    result = await asyncio.wait_for(models.embed(database['embed_model_id'], batch), timeout=30)
                    if (
                        getattr(result, 'binding_id', resolved.binding_id) != resolved.binding_id
                        or getattr(result, 'engine_model_snapshot', resolved.engine_model_snapshot)
                        != resolved.engine_model_snapshot
                    ):
                        raise KnowledgeError.reindex()
                    returned = sorted(result.response.vectors, key=lambda vector: vector.index)
                    if len(returned) != len(batch):
                        raise ValueError('invalid vector count')
                    vectors.extend([list(vector.values) for vector in returned])
            if (
                not vectors
                or not vectors[0]
                or any(
                    len(vector) != len(vectors[0]) or any(not math.isfinite(value) for value in vector)
                    for vector in vectors
                )
            ):
                raise ValueError('invalid vectors')
            return vectors
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def _index_document(self, database: dict, document: dict, upload: dict, params: ChunkParameters) -> dict:
        db_id, doc_id = database['db_id'], document['id']
        chunks = []
        old_chunks = document.get('chunks', [])
        async with self.metadata.transaction() as state:
            document.update(status='processing', error=None)
            state['documents'][doc_id] = document
        try:
            path = self._upload_path(upload)
            content = await asyncio.to_thread(process_file_to_markdown, path)
            chunks = split_text_into_chunks(content, doc_id, uuid4().hex[:12], params.chunk_size, params.chunk_overlap)
            resolved = await self._resolve(database['embed_model_id'], 'embedding')
            fingerprint = self._fingerprint(resolved)
            old_spec = database.get('index_spec')
            if old_spec and old_spec['fingerprint'] != fingerprint:
                raise KnowledgeError.reindex()
            vectors = await self._embed(database, [chunk['content'] for chunk in chunks], resolved)
            spec = {'model_id': database['embed_model_id'], 'fingerprint': fingerprint, 'dimension': len(vectors[0])}
            if old_spec and old_spec != spec:
                raise KnowledgeError.reindex()
            database['index_spec'] = spec
            document['chunks'] = old_chunks + chunks
            async with self.metadata.transaction() as state:
                state['documents'][doc_id] = document
                state['databases'][db_id]['index_spec'] = spec
            await self._store(database['kb_type']).upsert(db_id, chunks, vectors, spec)
            if database['enable_knowledge_graph']:
                document['graph_attempted'] = True
                async with self.metadata.transaction() as state:
                    state['documents'][doc_id] = document
                graph = await self._graph(database)
                if old_chunks:
                    await graph.delete_document(doc_id)
                await graph.insert(doc_id, content, document['file_name'])
            if old_chunks:
                await self._store(database['kb_type']).delete_chunks(db_id, [chunk['id'] for chunk in old_chunks])
            document.update(
                status='done',
                chunks=chunks,
                chunk_size=params.chunk_size,
                chunk_overlap=params.chunk_overlap,
                error=None,
            )
            async with self.metadata.transaction() as state:
                state['documents'][doc_id] = document
                state['databases'][db_id]['index_spec'] = spec
            database['index_spec'] = spec
        except BaseException as error:
            document.update(
                status='failed',
                chunks=old_chunks + chunks,
                error=(
                    error.public_dict()
                    if isinstance(error, KnowledgeError)
                    else KnowledgeError.unavailable().public_dict()
                ),
            )
            async with self.metadata.transaction() as state:
                state['documents'][doc_id] = document
            if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
        return {key: document.get(key) for key in _DOC_FIELDS}

    async def add_documents(self, db_id: str, upload_ids: Sequence[str], params: ChunkParameters) -> dict:
        _check_id(db_id, 'kb')
        if not 1 <= len(upload_ids) <= MAX_BINDINGS or len(set(upload_ids)) != len(upload_ids):
            raise KnowledgeError('KNOWLEDGE_UPLOAD_INVALID', '上传文件标识无效')
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            database = self._database(state, db_id)
            uploads = []
            for upload_id in upload_ids:
                _check_id(upload_id, 'upl')
                upload = state['uploads'].get(upload_id)
                if not upload or upload['db_id'] != db_id:
                    raise KnowledgeError.missing()
                if any(document['upload_id'] == upload_id for document in state['documents'].values()):
                    raise KnowledgeError('KNOWLEDGE_ALREADY_INDEXED', '文件已添加，请使用重新切片操作', 409)
                uploads.append(upload)
            results = []
            for upload in uploads:
                document = {
                    'id': _id('doc'),
                    'db_id': db_id,
                    'upload_id': upload['upload_id'],
                    'file_name': upload['file_name'],
                    'type': upload['extension'][1:],
                    'file_size': upload['file_size'],
                    'created_at': _now(),
                    'folder_id': upload['folder_id'],
                    'chunks': [],
                }
                results.append(await self._index_document(database, document, upload, params))
            succeeded = sum(result['status'] == 'done' for result in results)
            return {
                'status': 'success' if succeeded == len(results) else 'partial' if succeeded else 'failed',
                'items': results,
            }

    async def preview(self, db_id: str, doc_id: str) -> dict:
        state = await self.metadata.read()
        self._database(state, db_id)
        document = self._document(state, db_id, doc_id)
        upload = state['uploads'].get(document['upload_id'])
        if not upload or upload['db_id'] != db_id:
            raise KnowledgeError.missing()
        try:
            return {'content': await asyncio.to_thread(process_file_to_markdown, self._upload_path(upload))}
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def rechunk(self, db_id: str, doc_id: str, params: ChunkParameters) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            database = self._database(state, db_id)
            document = self._document(state, db_id, doc_id)
            result = await self._index_document(database, document, state['uploads'][document['upload_id']], params)
            return {'status': 'success' if result['status'] == 'done' else 'failed', 'items': [result]}

    async def _delete_document(self, state: dict, database: dict, document: dict) -> None:
        db_id = database['db_id']
        try:
            await self._store(database['kb_type']).delete_chunks(
                db_id, [chunk['id'] for chunk in document.get('chunks', [])]
            )
            if database['enable_knowledge_graph'] and document.get('graph_attempted'):
                await (await self._graph(database)).delete_document(document['id'])
        except Exception:
            raise KnowledgeError.unavailable() from None
        upload = state['uploads'].get(document['upload_id'])
        async with self.metadata.transaction() as current:
            current['documents'].pop(document['id'], None)
            current['uploads'].pop(document['upload_id'], None)
        if upload:
            self._upload_path(upload).unlink(missing_ok=True)

    async def delete_document(self, db_id: str, doc_id: str) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            database = self._database(state, db_id)
            document = self._document(state, db_id, doc_id)
            await self._delete_document(state, database, document)
        return {'deleted': True}

    async def _retrieve_database(self, db_id: str, query: str, top_k: int) -> list[KnowledgeHit]:
        hits = []
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            database = self._database(state, db_id)
            resolved = await self._resolve(database['embed_model_id'], 'embedding')
            spec = database.get('index_spec')
            if spec is None:
                if any(document['db_id'] == db_id for document in state['documents'].values()):
                    raise KnowledgeError.unavailable()
                return []
            if spec['fingerprint'] != self._fingerprint(resolved):
                raise KnowledgeError.reindex()
            vectors = await self._embed(database, [query], resolved)
            if len(vectors[0]) != spec['dimension']:
                raise KnowledgeError.reindex()
            try:
                rows = await self._store(database['kb_type']).query(db_id, vectors[0], top_k, spec)
            except KnowledgeError:
                raise
            except Exception:
                raise KnowledgeError.unavailable() from None
            for row in rows:
                document = state['documents'].get(row.get('doc_id'))
                if not document or document['db_id'] != db_id:
                    raise KnowledgeError.unavailable()
                if document['status'] != 'done':
                    # An incomplete indexing attempt may have inserted vectors; it is never context.
                    continue
                chunk = next((item for item in document['chunks'] if item['id'] == row.get('id')), None)
                if not chunk:
                    continue  # Old revision retained after a failed storage cleanup.
                if row.get('content') != chunk['content']:
                    raise KnowledgeError.unavailable()
                score = row.get('score', 0.0)
                if not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise KnowledgeError.unavailable()
                hits.append(KnowledgeHit(chunk['content'], document['id'], document['file_name'], db_id, score))
        return hits

    async def retrieve_context(
        self, *, owner_user_id: int, knowledge_base_ids: Sequence[str], query: str, top_k: int = 8
    ) -> list[KnowledgeHit]:
        # Validate authorization, scope, provenance and budget as one fail-closed boundary.
        try:
            allowed = await self.owner_authorizer(owner_user_id)
        except Exception:
            raise KnowledgeError.unavailable() from None
        if not allowed:
            raise KnowledgeError('KNOWLEDGE_FORBIDDEN', '当前用户没有知识库使用权限', 403)
        if isinstance(knowledge_base_ids, (str, bytes)) or len(knowledge_base_ids) > MAX_BINDINGS:
            raise KnowledgeError('KNOWLEDGE_BINDINGS_INVALID', '知识库绑定数量或格式无效')
        if not isinstance(query, str) or not query.strip() or len(query.encode('utf-8')) > MAX_CONTEXT_BYTES:
            raise KnowledgeError('KNOWLEDGE_QUERY_INVALID', '查询内容无效')
        if type(top_k) is not int or not 1 <= top_k <= MAX_PASSAGES:
            raise KnowledgeError('KNOWLEDGE_QUERY_INVALID', '检索数量无效')
        ids = list(dict.fromkeys(knowledge_base_ids))
        state = await self.metadata.read()
        for db_id in ids:
            self._database(state, db_id)
        hits = []
        for db_id in ids:
            hits.extend(await self._retrieve_database(db_id, query, top_k))
        selected, used_bytes = [], 0
        for hit in sorted(hits, key=lambda item: item.score, reverse=True):
            overhead = sum(len(value.encode('utf-8')) for value in (hit.source_id, hit.title, hit.knowledge_base_id))
            available = MAX_CONTEXT_BYTES - used_bytes - overhead
            if available <= 0 or len(selected) >= top_k:
                break
            content = hit.content.encode('utf-8')[:available].decode('utf-8', errors='ignore')
            if not content.strip():
                continue
            selected.append(KnowledgeHit(content, hit.source_id, hit.title, hit.knowledge_base_id, hit.score))
            used_bytes += overhead + len(content.encode('utf-8'))
        return selected

    async def query(self, owner_user_id: int, db_id: str, query: str, params: QueryParameters) -> dict:
        hits = await self.retrieve_context(
            owner_user_id=owner_user_id, knowledge_base_ids=[db_id], query=query, top_k=params.top_k
        )
        return {
            'status': 'success',
            'result': [
                dict(asdict(hit), metadata={'file_id': hit.source_id, 'source': hit.title})
                for hit in hits
                if hit.score >= params.similarity_threshold
            ],
        }

    async def list_folders(self, db_id: str) -> dict:
        state = await self.metadata.read()
        self._database(state, db_id)
        return {'folders': [folder for folder in state['folders'].values() if folder['db_id'] == db_id]}

    async def create_folder(self, db_id: str, name: str, parent_id: str | None = None) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id), self.metadata.transaction() as state:
            self._database(state, db_id)
            self._folder(state, db_id, parent_id)
            folder = {'id': _id('folder'), 'db_id': db_id, 'name': name, 'parent_id': parent_id, 'created_at': _now()}
            state['folders'][folder['id']] = folder
        return folder

    async def rename_folder(self, db_id: str, folder_id: str, name: str) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id), self.metadata.transaction() as state:
            self._database(state, db_id)
            folder = self._folder(state, db_id, folder_id)
            folder['name'] = name
        return folder

    async def delete_folder(self, db_id: str, folder_id: str) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            state = await self.metadata.read()
            database = self._database(state, db_id)
            self._folder(state, db_id, folder_id)
            removed = {folder_id}
            while True:
                children = {
                    folder['id']
                    for folder in state['folders'].values()
                    if folder['db_id'] == db_id and folder['parent_id'] in removed
                }
                if children <= removed:
                    break
                removed |= children
            for document in list(state['documents'].values()):
                if document['db_id'] == db_id and document.get('folder_id') in removed:
                    await self._delete_document(state, database, document)
            async with self.metadata.transaction() as current:
                for identifier in removed:
                    current['folders'].pop(identifier, None)
                uploads = [
                    upload
                    for upload in current['uploads'].values()
                    if upload['db_id'] == db_id and upload.get('folder_id') in removed
                ]
                for upload in uploads:
                    self._upload_path(upload).unlink(missing_ok=True)
                    current['uploads'].pop(upload['upload_id'])
        return {'deleted_ids': sorted(removed)}

    async def _graph(self, database: dict) -> Any:
        if not database['enable_knowledge_graph']:
            raise KnowledgeError('KNOWLEDGE_GRAPH_DISABLED', '该知识库未启用知识图谱', 409)
        from plugins.ai.knowledge.graph import KnowledgeGraph

        db_id = database['db_id']
        if db_id not in self._graphs:
            self._graphs[db_id] = KnowledgeGraph(self.data_dir, database, self.model_service_factory)
        self._graphs[db_id].database = database
        return self._graphs[db_id]

    async def _checked_graph_database(self, db_id: str) -> dict:
        state = await self.metadata.read()
        database = self._database(state, db_id)
        if not database['enable_knowledge_graph']:
            raise KnowledgeError('KNOWLEDGE_GRAPH_DISABLED', '该知识库未启用知识图谱', 409)
        if any(document['db_id'] == db_id and document['status'] != 'done' for document in state['documents'].values()):
            raise KnowledgeError('KNOWLEDGE_GRAPH_INDEX_INCOMPLETE', '文档索引未完整完成，请重试或删除失败文档', 503)
        resolved = await self._resolve(database['embed_model_id'], 'embedding')
        await self._resolve(database['llm_model_id'], 'chat')
        spec = database.get('index_spec')
        if spec and spec['fingerprint'] != self._fingerprint(resolved):
            raise KnowledgeError.reindex()
        return database

    async def graph_labels(self, db_id: str) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            database = await self._checked_graph_database(db_id)
            if not database.get('index_spec'):
                return {'labels': []}
            return {'labels': await (await self._graph(database)).labels()}

    async def graph_subgraph(self, db_id: str, node_label: str, max_depth: int, max_nodes: int) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            database = await self._checked_graph_database(db_id)
            if not database.get('index_spec'):
                return {'nodes': [], 'edges': [], 'is_truncated': False}
            return await (await self._graph(database)).subgraph(node_label, max_depth, max_nodes)

    async def graph_query(self, db_id: str, query: str, params: QueryParameters) -> dict:
        _check_id(db_id, 'kb')
        async with self.metadata.lock(db_id):
            database = await self._checked_graph_database(db_id)
            if not database.get('index_spec'):
                return {'status': 'success', 'result': ''}
            result = await (await self._graph(database)).query(query, params)
            return {'status': 'success', 'result': result}
