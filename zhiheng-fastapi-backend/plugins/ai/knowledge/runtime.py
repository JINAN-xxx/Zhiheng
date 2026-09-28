# ruff: noqa: PLC0415
# Optional storage dependencies are deliberately imported at first use.
"""Authenticated platform boundary around the selected knowledge algorithms."""

import asyncio
import math
import re
import shutil
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from module_Knowledge.src.knowledge.indexing import SUPPORTED_EXTENSIONS, process_file_to_markdown
from module_Knowledge.src.knowledge.kb_utils import normalize_embedding_text, split_text_into_chunks
from plugins.ai.knowledge.graph_config import database_graph_config, model_snapshot
from plugins.ai.knowledge.graph_errors import safe_graph_error
from plugins.ai.knowledge.graph_jobs import GraphConfigurationRuntime
from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.knowledge.models import (
    KNOWLEDGE_EMBEDDING_BATCH_SIZE,
    ChunkParameters,
    DatabaseCreate,
    GraphBuildRequest,
    GraphConfiguration,
    KnowledgeError,
    KnowledgeHit,
    QueryParameters,
    embedding_fingerprint,
)

ModelFactory = Callable[[], AbstractAsyncContextManager[Any]]
MAX_FILENAME_LENGTH = 200
MAX_BINDINGS = 20
MAX_CONTEXT_BYTES = 24000
MAX_PASSAGES = 8
_ACTIVE_GRAPH_STATES = {'queued', 'running'}
# Application workers are intentionally limited to one. Share task ownership between
# runtime instances in that process, while retaining durable progress across restarts.
_ACTIVE_GRAPH_JOBS: dict[tuple[Path, str], asyncio.Task] = {}

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
    'graph_build',
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
    'graph_status',
    'graph_error',
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex}'


def _check_id(value: str, prefix: str) -> str:
    if not isinstance(value, str) or not _RESOURCE_ID.fullmatch(value) or not value.startswith(prefix + '_'):
        raise KnowledgeError.missing()
    return value


class KnowledgeRuntime(GraphConfigurationRuntime):
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
        self._graph_tasks: dict[str, asyncio.Task] = {}
        self._closing = False

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

        capabilities = available_types()
        return {
            'kb_types': {'milvus': capabilities['milvus']},
            'graph_available': capabilities['lightrag']['available'],
        }

    async def close(self) -> None:
        self._closing = True
        tasks = tuple(self._graph_tasks.items())
        for _, task in tasks:
            task.cancel()
        await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        # A task cancelled before its coroutine starts cannot run its finally block.
        for job_id, _ in tasks:
            await self._interrupt_graph_build(job_id)
            await self._interrupt_graph_preview(job_id)
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
        result = {key: database.get(key) for key in _DB_FIELDS}
        result['graph_build'] = KnowledgeRuntime._graph_build(database)
        result['graph_readable'] = KnowledgeRuntime._graph_readable(database)
        return result

    @staticmethod
    def _graph_build(database: dict) -> dict:
        return dict(
            database.get('graph_build')
            or {
                'status': 'idle' if database['enable_knowledge_graph'] else 'disabled',
                'job_id': None,
                'total_documents': 0,
                'processed_documents': 0,
                'failed_documents': 0,
                'current_document': None,
                'error': None,
                'started_at': None,
                'finished_at': None,
            }
        )

    @staticmethod
    def _document_graph_status(database: dict, document: dict) -> str:
        if document.get('graph_status'):
            return document['graph_status']
        if not database['enable_knowledge_graph']:
            return 'disabled'
        # Before separate graph jobs existed, a successful graph attempt was part
        # of the document's synchronous index transaction.
        return 'completed' if document.get('graph_attempted') and document['status'] == 'done' else 'pending'

    @staticmethod
    def _graph_error(code: str = 'KNOWLEDGE_GRAPH_BUILD_FAILED') -> dict:
        messages = {
            'KNOWLEDGE_GRAPH_BUILD_FAILED': '知识图谱构建失败，向量检索仍可使用，请重试',
            'KNOWLEDGE_GRAPH_INTERRUPTED': '知识图谱构建已中断，请重试以继续处理未完成文档',
            'KNOWLEDGE_GRAPH_CLEANUP_FAILED': '知识图谱构建失败且清理未完成，重试时将先清理残留数据',
        }
        return {'code': code, 'message': messages[code]}

    @staticmethod
    def _ensure_graph_idle(database: dict) -> None:
        if (
            KnowledgeRuntime._graph_build(database)['status'] in _ACTIVE_GRAPH_STATES
            or database.get('graph_preview', {}).get('status') in _ACTIVE_GRAPH_STATES
        ):
            raise KnowledgeError('KNOWLEDGE_GRAPH_BUSY', '知识图谱正在构建中，请完成后再修改文档', 409)

    @asynccontextmanager
    async def _document_mutation(self, db_id: str) -> AsyncIterator[None]:
        self._ensure_graph_idle(self._database(await self._graph_state(), db_id))
        # Wait for graph readers before acquiring the lock used by vector queries.
        # Mutations recheck the durable job state once both locks are held.
        async with self.metadata.lock(f'graph-{db_id}'), self.metadata.lock(db_id):
            yield

    async def _graph_state(self) -> dict:
        state = await self.metadata.read()
        legacy = [
            database['db_id']
            for database in state['databases'].values()
            if database.get('enable_knowledge_graph')
            and not database.get('graph_build')
            and not database.get('graph_versions')
        ]
        if legacy:
            async with self.metadata.transaction() as state:
                for db_id in legacy:
                    database = state['databases'].get(db_id)
                    if not database or database.get('graph_build'):
                        continue
                    documents = [doc for doc in state['documents'].values() if doc['db_id'] == db_id]
                    if documents and all(
                        self._document_graph_status(database, doc) == 'completed' for doc in documents
                    ):
                        database['graph_build'] = dict(
                            self._graph_build(database),
                            status='completed',
                            total_documents=len(documents),
                            processed_documents=len(documents),
                        )
        orphaned = {
            build['job_id']
            for database in state['databases'].values()
            if (build := self._graph_build(database))['status'] in _ACTIVE_GRAPH_STATES
            and not self._live_graph_job(build['job_id'])
        }
        if orphaned:
            async with self.metadata.transaction() as state:
                for database in state['databases'].values():
                    build = self._graph_build(database)
                    if build['job_id'] in orphaned and not self._live_graph_job(build['job_id']):
                        self._mark_graph_interrupted(state, database, build)
        preview_orphans = [
            database['graph_preview']['job_id']
            for database in state['databases'].values()
            if database.get('graph_preview', {}).get('status') in _ACTIVE_GRAPH_STATES
            and not self._live_graph_job(database['graph_preview']['job_id'])
        ]
        for job_id in preview_orphans:
            await self._interrupt_graph_preview(job_id)
        return await self.metadata.read() if preview_orphans else state

    async def _interrupt_graph_preview(self, job_id: str) -> None:
        async with self.metadata.transaction() as state:
            for database in state['databases'].values():
                preview = database.get('graph_preview', {})
                if preview.get('job_id') == job_id and preview.get('status') in _ACTIVE_GRAPH_STATES:
                    preview.update(
                        status='interrupted', error=self._graph_error('KNOWLEDGE_GRAPH_INTERRUPTED'), finished_at=_now()
                    )

    def _live_graph_job(self, job_id: str) -> bool:
        task = _ACTIVE_GRAPH_JOBS.get((self.data_dir, job_id))
        return task is not None and not task.done()

    def _mark_graph_interrupted(self, state: dict, database: dict, build: dict) -> None:
        if build['status'] not in _ACTIVE_GRAPH_STATES:
            return
        error = self._graph_error('KNOWLEDGE_GRAPH_INTERRUPTED')
        build.update(status='interrupted', error=error, current_document=None, finished_at=_now())
        database['graph_build'] = build
        for document in state['documents'].values():
            if document['db_id'] == database['db_id'] and self._document_graph_status(database, document) in {
                'pending',
                'running',
            }:
                document.update(graph_status='interrupted', graph_error=error)

    async def _interrupt_graph_build(self, job_id: str) -> None:
        async with self.metadata.transaction() as state:
            for database in state['databases'].values():
                build = self._graph_build(database)
                if build['job_id'] == job_id:
                    self._mark_graph_interrupted(state, database, build)

    async def graph_build_status(self, db_id: str) -> dict:
        _check_id(db_id, 'kb')
        database = self._database(await self._graph_state(), db_id)
        build = self._graph_build(database)
        build['active_revision'] = self._active_graph_revision(database)
        build['graph_readable'] = self._graph_readable(database)
        graph = self._graphs.get(build.get('revision'))
        current = build.get('current_document')
        if graph and current and build['status'] == 'running' and hasattr(graph, 'progress'):
            try:
                progress = await asyncio.wait_for(graph.progress(current['id']), timeout=5)
            except Exception:
                progress = build.get('progress')
            if progress:
                build['progress'] = progress
                async with self.metadata.transaction() as state:
                    latest = self._current_graph_job(state, db_id, build['job_id'])
                    if latest and latest['graph_build'].get('current_document') == current:
                        latest['graph_build']['progress'] = progress
        return build

    @staticmethod
    def _graph_model(database: dict, payload: GraphBuildRequest) -> str:
        config = database_graph_config(database)
        current = config.llm_model_id if config else None
        if current and payload.llm_model_id and current != payload.llm_model_id and not payload.rebuild:
            raise KnowledgeError(
                'KNOWLEDGE_GRAPH_MODEL_MIGRATION_REQUIRED',
                '知识图谱模型变更需要显式重新构建',
                409,
            )
        model_id = payload.llm_model_id or current
        if not model_id:
            raise KnowledgeError('KNOWLEDGE_GRAPH_MODEL_REQUIRED', '首次启用知识图谱时必须选择对话模型')
        return model_id

    async def start_graph_build(self, db_id: str, payload: GraphBuildRequest) -> dict:
        _check_id(db_id, 'kb')
        if self._closing:
            raise KnowledgeError.unavailable()
        database = self._database(await self._graph_state(), db_id)
        model_id = self._graph_model(database, payload)
        configuration_before = database_graph_config(database)
        build = self._graph_build(database)
        if build['status'] in _ACTIVE_GRAPH_STATES:
            return build
        from plugins.ai.knowledge.stores import require_graph

        require_graph()
        resolved = await self._resolve(model_id, 'chat')
        if database.get('index_spec'):
            await self._check_embedding_spec(database)
        async with self.metadata.lock(db_id):
            database = self._database(await self._graph_state(), db_id)
            if self._graph_build(database)['status'] in _ACTIVE_GRAPH_STATES:
                return self._graph_build(database)
            if database_graph_config(database) != configuration_before:
                raise KnowledgeError('KNOWLEDGE_GRAPH_CONFIG_CHANGED', '图谱配置刚刚发生变化，请刷新后重试', 409)
            self._graph_model(database, payload)
            return await self._queue_graph_build(db_id, model_id, rebuild=payload.rebuild, resolved=resolved)

    async def _queue_graph_build(
        self, db_id: str, model_id: str | None = None, *, rebuild: bool = False, resolved: Any = None
    ) -> dict:
        if self._closing:
            raise KnowledgeError.unavailable()
        database = self._database(await self.metadata.read(), db_id)
        configuration_status = self._graph_config_result(database)
        if model_id is None and configuration_status['requires_rebuild'] and not configuration_status['can_resume']:
            # 上传先完成主向量索引；草稿配置由显式重建发布，不能把成功上传变成接口失败。
            return self._graph_build(database)
        config = database_graph_config(database)
        if config is None:
            config = GraphConfiguration(llm_model_id=model_id)
        elif model_id and model_id != config.llm_model_id:
            config = config.model_copy(update={'llm_model_id': model_id})
        resolved = resolved or await self._resolve(config.llm_model_id, 'chat')
        try:
            revision = await self._prepare_graph_version(db_id, config, resolved, rebuild=rebuild)
        except KnowledgeError as error:
            if error.code != 'KNOWLEDGE_GRAPH_REBUILD_REQUIRED':
                raise
            async with self.metadata.transaction() as state:
                self._database(state, db_id)['graph_rebuild_required'] = True
            if model_id is not None:
                raise
            return self._graph_build(database)
        job_id = uuid4().hex
        async with self.metadata.transaction() as state:
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
            documents = [document for document in state['documents'].values() if document['db_id'] == db_id]
            completed = 0
            for document in documents:
                if self._document_graph_status(database, document) == 'completed':
                    completed += 1
                else:
                    document.update(graph_status='pending', graph_error=None)
            build = dict(
                self._graph_build(database),
                status='queued',
                job_id=job_id,
                revision=revision,
                total_documents=len(documents),
                processed_documents=completed,
                failed_documents=0,
                current_document=None,
                error=None,
                started_at=None,
                finished_at=None,
                progress=None,
            )
            database['graph_build'] = build
        task = asyncio.create_task(self._run_graph_build(db_id, job_id), name=f'knowledge-graph-{job_id}')
        self._register_graph_task(job_id, task)
        return dict(build)

    def _register_graph_task(self, job_id: str, task: asyncio.Task) -> None:
        self._graph_tasks[job_id] = task
        _ACTIVE_GRAPH_JOBS[self.data_dir, job_id] = task

        def finished(completed_task: asyncio.Task) -> None:
            self._graph_tasks.pop(job_id, None)
            _ACTIVE_GRAPH_JOBS.pop((self.data_dir, job_id), None)
            if not completed_task.cancelled():
                completed_task.exception()

        task.add_done_callback(finished)

    @staticmethod
    def _current_graph_job(state: dict, db_id: str, job_id: str) -> dict | None:
        database = state['databases'].get(db_id)
        if not database:
            return None
        build = database.get('graph_build', {})
        if build.get('job_id') != job_id or build.get('status') not in _ACTIVE_GRAPH_STATES:
            return None
        return database

    async def _build_graph_document(self, db_id: str, job_id: str, doc_id: str) -> bool:
        initial = self._database(await self.metadata.read(), db_id)
        revision = initial.get('graph_target_revision', db_id)
        lock_id = revision if self._active_graph_revision(initial) not in (None, revision) else db_id
        async with self.metadata.lock(f'graph-{lock_id}'):
            async with self.metadata.transaction() as state:
                database = self._current_graph_job(state, db_id, job_id)
                if database is None:
                    return False
                document = self._document(state, db_id, doc_id)
                document.update(graph_status='running', graph_error=None)
                database['graph_build']['current_document'] = {'id': doc_id, 'file_name': document['file_name']}
                database['graph_build']['progress'] = None
                attempted = document.get('graph_attempted', False)
                upload = state['uploads'].get(document['upload_id'])
                ready = document['status'] == 'done' and database.get('index_spec') and upload
            graph, error, cleaned, progress = None, None, False, None
            try:
                if not ready:
                    raise KnowledgeError('KNOWLEDGE_GRAPH_INDEX_INCOMPLETE', '请先完成文档的向量索引', 409)
                await self._check_embedding_spec(database)
                async with self.metadata.transaction() as state:
                    if self._current_graph_job(state, db_id, job_id) is None:
                        return False
                    # Persist intent only after validation, before any graph write.
                    state['documents'][doc_id]['graph_attempted'] = True
                    attempted_documents = state['databases'][db_id]['graph_versions'][revision].setdefault(
                        'attempted_documents', []
                    )
                    if doc_id not in attempted_documents:
                        attempted_documents.append(doc_id)
                view = self._graph_version_database(database, revision)
                snapshot = view.get('_graph_snapshot')
                if snapshot and model_snapshot(await self._resolve(view['llm_model_id'], 'chat')) != snapshot:
                    raise KnowledgeError('KNOWLEDGE_GRAPH_MODEL_CHANGED', '图谱模型绑定已变化，请重新构建图谱', 409)
                graph = await self._graph(view)
                if attempted:
                    await graph.delete_document(doc_id)
                content = await asyncio.to_thread(process_file_to_markdown, self._upload_path(upload))
                await graph.insert(doc_id, content, document['file_name'])
                progress = await self._read_graph_progress(graph, doc_id)
            except asyncio.CancelledError:
                # Preserve graph_attempted: retry cleans any partial model/storage writes.
                raise
            except Exception as failure:
                reindex = isinstance(failure, KnowledgeError) and failure.code == 'KNOWLEDGE_REINDEX_REQUIRED'
                known_error = safe_graph_error(failure)
                error = known_error.public_dict() if known_error else self._graph_error()
                if graph is not None:
                    try:
                        progress = await self._read_graph_progress(graph, doc_id)
                        await graph.delete_document(doc_id)
                        cleaned = True
                    except Exception:
                        if not reindex:
                            error = self._graph_error('KNOWLEDGE_GRAPH_CLEANUP_FAILED')
            return await self._record_graph_document_result(
                db_id, job_id, doc_id, {'error': error, 'cleaned': cleaned, 'progress': progress}
            )

    @staticmethod
    async def _read_graph_progress(graph: Any, doc_id: str) -> dict | None:
        if not hasattr(graph, 'progress'):
            return None
        try:
            return await asyncio.wait_for(graph.progress(doc_id), timeout=5)
        except Exception:
            # 进度读取故障不能改变已经提交的图谱结果。
            return None

    async def _record_graph_document_result(self, db_id: str, job_id: str, doc_id: str, outcome: dict) -> bool:
        error, cleaned, progress = outcome['error'], outcome['cleaned'], outcome['progress']
        async with self.metadata.transaction() as state:
            database = self._current_graph_job(state, db_id, job_id)
            if database is None:
                return False
            document = self._document(state, db_id, doc_id)
            document.update(graph_status='failed' if error else 'completed', graph_error=error)
            revision = database['graph_build'].get('revision')
            version = database.get('graph_versions', {}).get(revision)
            if version and not error and doc_id not in version['documents']:
                version['documents'].append(doc_id)
            if cleaned:
                document['graph_attempted'] = False
                if version:
                    version['attempted_documents'] = [
                        identifier for identifier in version.get('attempted_documents', []) if identifier != doc_id
                    ]
            build = database['graph_build']
            if progress:
                build['progress'] = progress
            build['processed_documents'] += 1
            build['failed_documents'] += int(error is not None)
            build['current_document'] = None
            if error and (not build['error'] or error['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'):
                build['error'] = error
        return True

    async def _run_graph_build(self, db_id: str, job_id: str) -> None:
        try:
            async with self.metadata.transaction() as state:
                database = self._current_graph_job(state, db_id, job_id)
                if database is None:
                    return
                database['graph_build'].update(status='running', started_at=_now())
                document_ids = [
                    document['id']
                    for document in state['documents'].values()
                    if document['db_id'] == db_id and self._document_graph_status(database, document) != 'completed'
                ]
            for doc_id in document_ids:
                if not await self._build_graph_document(db_id, job_id, doc_id):
                    return
            async with self.metadata.transaction() as state:
                database = self._current_graph_job(state, db_id, job_id)
                if database is not None:
                    build = database['graph_build']
                    failed = build['failed_documents'] > 0
                    if not failed:
                        revision = build.get('revision')
                        version = database.get('graph_versions', {}).get(revision)
                        if version:
                            database['graph_active_revision'] = revision
                            database['llm_model_id'] = version['config']['llm_model_id']
                    build.update(
                        status='failed' if failed else 'completed',
                        finished_at=_now(),
                        error=(build['error'] or self._graph_error()) if failed else None,
                        current_document=None,
                    )
        except asyncio.CancelledError:
            await self._interrupt_graph_build(job_id)
            raise
        except Exception:
            async with self.metadata.transaction() as state:
                database = self._current_graph_job(state, db_id, job_id)
                if database is not None:
                    error = self._graph_error()
                    database['graph_build'].update(
                        status='failed', error=error, current_document=None, finished_at=_now()
                    )
                    for document in state['documents'].values():
                        if document['db_id'] == db_id and document.get('graph_status') in {'pending', 'running'}:
                            document.update(graph_status='failed', graph_error=error)

    async def list_databases(self) -> dict:
        state = await self._graph_state()
        return {'databases': [self._safe_database(database) for database in state['databases'].values()]}

    async def get_database(self, db_id: str) -> dict:
        return self._safe_database(self._database(await self._graph_state(), db_id))

    @staticmethod
    def _fingerprint(resolved: Any) -> str:
        return embedding_fingerprint(resolved)

    async def _check_embedding_spec(self, database: dict) -> None:
        resolved = await self._resolve(database['embed_model_id'], 'embedding')
        spec = database.get('index_spec')
        if spec and (
            spec['model_id'] != database['embed_model_id'] or spec['fingerprint'] != self._fingerprint(resolved)
        ):
            raise KnowledgeError.reindex()

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
        database['graph_build'] = self._graph_build(database)
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
        async with self._document_mutation(db_id):
            state = await self._graph_state()
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
            try:
                await self._store(database['kb_type']).delete_database(db_id)
                if database['enable_knowledge_graph']:
                    graph_documents = [
                        document['id']
                        for document in state['documents'].values()
                        if document['db_id'] == db_id and document.get('graph_attempted')
                    ]
                    if database.get('index_spec'):
                        for revision in database.get('graph_versions') or [db_id]:
                            graph = await self._graph(self._graph_version_database(database, revision))
                            await graph.delete_database(graph_documents)
                            await graph.close()
                            self._graphs.pop(revision, None)
                preview = database.get('graph_preview')
                if preview and preview.get('cleanup_pending'):
                    await self._cleanup_graph_preview(database, preview)
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
            state = await self._graph_state()
            self._ensure_graph_idle(self._database(state, db_id))
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
        state = await self._graph_state()
        database = self._database(state, db_id)
        documents = [
            dict(
                {key: document.get(key) for key in _DOC_FIELDS},
                graph_status=self._document_graph_status(database, document),
            )
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
                for offset in range(0, len(texts), KNOWLEDGE_EMBEDDING_BATCH_SIZE):
                    batch = texts[offset : offset + KNOWLEDGE_EMBEDDING_BATCH_SIZE]
                    result = await asyncio.wait_for(models.embed(database['embed_model_id'], batch), timeout=30)
                    if (
                        getattr(result, 'binding_id', resolved.binding_id) != resolved.binding_id
                        or getattr(result, 'engine_model_snapshot', resolved.engine_model_snapshot)
                        != resolved.engine_model_snapshot
                    ):
                        raise KnowledgeError.reindex()
                    returned = sorted(result.response.vectors, key=lambda vector: vector.index)
                    if [vector.index for vector in returned] != list(range(len(batch))):
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
            if old_chunks:
                await self._store(database['kb_type']).delete_chunks(db_id, [chunk['id'] for chunk in old_chunks])
            document.update(
                status='done',
                chunks=chunks,
                chunk_size=params.chunk_size,
                chunk_overlap=params.chunk_overlap,
                error=None,
                graph_status='pending' if database['enable_knowledge_graph'] else 'disabled',
                graph_error=None,
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
        async with self._document_mutation(db_id):
            state = await self._graph_state()
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
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
                    'graph_status': 'pending' if database['enable_knowledge_graph'] else 'disabled',
                    'graph_error': None,
                }
                results.append(await self._index_document(database, document, upload, params))
            succeeded = sum(result['status'] == 'done' for result in results)
            if database['enable_knowledge_graph'] and succeeded:
                await self._queue_graph_build(db_id)
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
        async with self._document_mutation(db_id):
            state = await self._graph_state()
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
            document = self._document(state, db_id, doc_id)
            result = await self._index_document(database, document, state['uploads'][document['upload_id']], params)
            if database['enable_knowledge_graph'] and result['status'] == 'done':
                await self._queue_graph_build(db_id)
            return {'status': 'success' if result['status'] == 'done' else 'failed', 'items': [result]}

    async def _delete_document(self, state: dict, database: dict, document: dict) -> None:
        db_id = database['db_id']
        try:
            await self._store(database['kb_type']).delete_chunks(
                db_id, [chunk['id'] for chunk in document.get('chunks', [])]
            )
            if database['enable_knowledge_graph'] and (
                database.get('graph_versions') or document.get('graph_attempted')
            ):
                for revision in database.get('graph_versions') or [db_id]:
                    version = database.get('graph_versions', {}).get(revision)
                    contributed = (
                        document.get('graph_attempted')
                        if version is None
                        else (
                            document['id'] in version.get('documents', [])
                            or document['id'] in version.get('attempted_documents', [])
                        )
                    )
                    if contributed:
                        await (await self._graph(self._graph_version_database(database, revision))).delete_document(
                            document['id']
                        )
        except Exception:
            raise KnowledgeError.unavailable() from None
        upload = state['uploads'].get(document['upload_id'])
        async with self.metadata.transaction() as current:
            current['documents'].pop(document['id'], None)
            current['uploads'].pop(document['upload_id'], None)
            for version in current['databases'][db_id].get('graph_versions', {}).values():
                version['documents'] = [
                    identifier for identifier in version['documents'] if identifier != document['id']
                ]
                version['attempted_documents'] = [
                    identifier for identifier in version.get('attempted_documents', []) if identifier != document['id']
                ]
        if upload:
            self._upload_path(upload).unlink(missing_ok=True)

    async def delete_document(self, db_id: str, doc_id: str) -> dict:
        _check_id(db_id, 'kb')
        async with self._document_mutation(db_id):
            state = await self._graph_state()
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
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
        async with self._document_mutation(db_id):
            state = await self._graph_state()
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
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

        if '_graph_workspace' not in database:
            database = self._graph_version_database(database)
        db_id = database.get('_graph_workspace', database['db_id'])
        if db_id not in self._graphs:
            self._graphs[db_id] = KnowledgeGraph(self.data_dir, database, self.model_service_factory)
        self._graphs[db_id].database = database
        return self._graphs[db_id]

    async def _checked_graph_database(self, db_id: str, *, resolve_models: bool = True) -> dict:
        state = await self._graph_state()
        database = self._database(state, db_id)
        if not database['enable_knowledge_graph']:
            raise KnowledgeError('KNOWLEDGE_GRAPH_DISABLED', '该知识库未启用知识图谱', 409)
        active = self._active_graph_revision(database)
        separate_active = self._graph_readable(database)
        if not separate_active and (
            self._graph_build(database)['status'] in _ACTIVE_GRAPH_STATES
            or any(
                document['db_id'] == db_id and self._document_graph_status(database, document) != 'completed'
                for document in state['documents'].values()
            )
        ):
            raise KnowledgeError(
                'KNOWLEDGE_GRAPH_INDEX_INCOMPLETE', '知识图谱尚未构建完成，请先构建或重试失败文档', 409
            )
        database = self._graph_version_database(database, active)
        if not resolve_models:
            return database
        resolved = await self._resolve(database['embed_model_id'], 'embedding')
        await self._resolve(database['llm_model_id'], 'chat')
        spec = database.get('index_spec')
        if spec and spec['fingerprint'] != self._fingerprint(resolved):
            raise KnowledgeError.reindex()
        return database

    @asynccontextmanager
    async def _graph_read(self, db_id: str) -> AsyncIterator[dict]:
        # Fail fast during a build, then recheck after waiting for other graph I/O.
        await self._checked_graph_database(db_id, resolve_models=False)
        async with self.metadata.lock(f'graph-{db_id}'):
            yield await self._checked_graph_database(db_id)

    async def graph_labels(self, db_id: str) -> dict:
        _check_id(db_id, 'kb')
        async with self._graph_read(db_id) as database:
            if not database.get('index_spec'):
                return {'labels': []}
            return {'labels': await (await self._graph(database)).labels()}

    async def graph_subgraph(self, db_id: str, node_label: str, max_depth: int, max_nodes: int) -> dict:
        _check_id(db_id, 'kb')
        async with self._graph_read(db_id) as database:
            if not database.get('index_spec'):
                return {'nodes': [], 'edges': [], 'is_truncated': False}
            return await (await self._graph(database)).subgraph(node_label, max_depth, max_nodes)

    async def graph_query(self, db_id: str, query: str, params: QueryParameters) -> dict:
        _check_id(db_id, 'kb')
        async with self._graph_read(db_id) as database:
            if not database.get('index_spec'):
                return {'status': 'success', 'result': ''}
            result = await (await self._graph(database)).query(query, params)
            return {'status': 'success', 'result': result}
