# ruff: noqa: PLC0415
"""图谱配置与版本元数据；模型和存储调用由知识库运行时持有。"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from plugins.ai.knowledge.graph_config import database_graph_config, graph_fingerprint, model_snapshot
from plugins.ai.knowledge.graph_errors import safe_graph_error
from plugins.ai.knowledge.models import GraphConfiguration, GraphPreviewRequest, KnowledgeError


class GraphConfigurationRuntime:
    @staticmethod
    def _active_graph_revision(database: dict) -> str | None:
        if database.get('graph_active_revision'):
            return database['graph_active_revision']
        if not database.get('graph_versions') and database.get('graph_build', {}).get('status') == 'completed':
            return database['db_id']
        return None

    @staticmethod
    def _graph_version_database(database: dict, revision: str | None = None) -> dict:
        revision = revision or GraphConfigurationRuntime._active_graph_revision(database)
        version = database.get('graph_versions', {}).get(revision)
        if not version:
            view = dict(database, _graph_workspace=revision or database['db_id'])
            if database.get('llm_model_id'):
                view['_graph_config'] = GraphConfiguration(llm_model_id=database['llm_model_id']).model_dump()
            return view
        return dict(
            database,
            llm_model_id=version['config']['llm_model_id'],
            _graph_config=version['config'],
            _graph_snapshot=version['model_snapshot'],
            _graph_workspace=revision,
        )

    @staticmethod
    def _graph_config_result(database: dict) -> dict:
        config = database_graph_config(database)
        active = GraphConfigurationRuntime._active_graph_revision(database)
        version = database.get('graph_versions', {}).get(active)
        active_config = (
            version['config']
            if version
            else (
                GraphConfiguration(llm_model_id=database['llm_model_id']).model_dump()
                if active and database.get('llm_model_id')
                else None
            )
        )
        target = database.get('graph_versions', {}).get(database.get('graph_target_revision'), {})
        return {
            'config': config.model_dump() if config else None,
            'active_config': active_config,
            'active_revision': active,
            'requires_rebuild': bool(
                active
                and config
                and (
                    config.model_dump() != active_config
                    or database.get('graph_rebuild_required')
                    or (version and target and version['fingerprint'] != target['fingerprint'])
                )
            ),
            'can_resume': bool(
                config
                and target.get('config') == config.model_dump()
                and database.get('graph_build', {}).get('status') in {'failed', 'interrupted'}
            ),
        }

    @staticmethod
    def _graph_readable(database: dict) -> bool:
        active = GraphConfigurationRuntime._active_graph_revision(database)
        return bool(
            active
            and (
                database.get('graph_build', {}).get('status') == 'completed'
                or active != database.get('graph_target_revision')
            )
        )

    async def graph_config_status(self, db_id: str) -> dict:
        database = self._database(await self._graph_state(), db_id)
        return self._graph_config_result(database)

    async def save_graph_config(self, db_id: str, config: GraphConfiguration) -> dict:
        database = self._database(await self._graph_state(), db_id)
        self._ensure_graph_idle(database)
        await self._resolve(config.llm_model_id, 'chat')
        async with self.metadata.lock(db_id), self.metadata.transaction() as state:
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
            database['graph_config'] = config.model_dump()
            return self._graph_config_result(database)

    async def _prepare_graph_version(
        self, db_id: str, config: GraphConfiguration, resolved: Any, *, rebuild: bool
    ) -> str:
        """调用者持有知识库修改锁；新版本只写候选指针，不发布活动版本。"""
        async with self.metadata.transaction() as state:
            database = self._database(state, db_id)
            self._ensure_graph_idle(database)
            versions = database.setdefault('graph_versions', {})
            documents = [doc for doc in state['documents'].values() if doc['db_id'] == db_id]
            # 兼容升级前已完成的 workspace，配置编辑不会覆盖其模型和查询数据。
            legacy_completed = database.get('graph_build', {}).get('status') == 'completed'
            legacy_present = database.get('enable_knowledge_graph') and (
                legacy_completed or any(doc.get('graph_attempted') for doc in documents)
            )
            if not versions and legacy_present:
                legacy = GraphConfiguration(llm_model_id=database['llm_model_id'])
                versions[db_id] = {
                    'config': legacy.model_dump(),
                    'model_snapshot': model_snapshot(resolved) if legacy.llm_model_id == config.llm_model_id else {},
                    'fingerprint': graph_fingerprint(legacy, resolved, database)
                    if legacy.llm_model_id == config.llm_model_id
                    else None,
                    'documents': [
                        doc['id'] for doc in documents if self._document_graph_status(database, doc) == 'completed'
                    ],
                    'attempted_documents': [doc['id'] for doc in documents if doc.get('graph_attempted')],
                }
                database['graph_target_revision'] = db_id
                if legacy_completed:
                    database['graph_active_revision'] = db_id
            fingerprint = graph_fingerprint(config, resolved, database)
            target = database.get('graph_target_revision')
            active = database.get('graph_active_revision')
            reusable = target if target in versions and versions[target]['fingerprint'] == fingerprint else None
            if active and not rebuild and reusable is None and versions[active]['fingerprint'] != fingerprint:
                raise KnowledgeError(
                    'KNOWLEDGE_GRAPH_REBUILD_REQUIRED', '图谱配置或模型绑定已变化，请重新构建图谱', 409
                )
            if rebuild or reusable is None:
                target = db_id if not versions else f'kb_{uuid4().hex}'
                versions[target] = {
                    'config': config.model_dump(),
                    'model_snapshot': model_snapshot(resolved),
                    'fingerprint': fingerprint,
                    'documents': [],
                    'attempted_documents': [],
                }
                for document in documents:
                    # 首次升级未完成任务仍在原 workspace 内恢复并保留已完成文档。
                    existing = target == db_id and self._document_graph_status(database, document) == 'completed'
                    if existing:
                        versions[target]['documents'].append(document['id'])
                    else:
                        document.update(
                            graph_status='pending',
                            graph_error=None,
                            graph_attempted=document.get('graph_attempted', False) if target == db_id else False,
                        )
            database.update(graph_config=config.model_dump(), graph_target_revision=target, enable_knowledge_graph=True)
            database.pop('graph_rebuild_required', None)
            if not active:
                database['llm_model_id'] = config.llm_model_id
            return target

    @staticmethod
    def _preview_result(database: dict) -> dict:
        preview = database.get('graph_preview') or {'status': 'idle'}
        fields = (
            'status',
            'job_id',
            'document_id',
            'file_name',
            'result',
            'error',
            'progress',
            'started_at',
            'finished_at',
        )
        return {field: preview.get(field) for field in fields}

    async def graph_preview_status(self, db_id: str) -> dict:
        database = self._database(await self._graph_state(), db_id)
        result = self._preview_result(database)
        preview = database.get('graph_preview', {})
        graph = self._graphs.get(preview.get('workspace'))
        if graph and result['status'] == 'running' and hasattr(graph, 'progress'):
            result['progress'] = await graph.progress(preview['probe_document_id'])
        return result

    async def start_graph_preview(self, db_id: str, payload: GraphPreviewRequest) -> dict:
        if self._closing:
            raise KnowledgeError.unavailable()
        database = self._database(await self._graph_state(), db_id)
        if database.get('graph_preview', {}).get('status') in {'queued', 'running'}:
            return self._preview_result(database)
        self._ensure_graph_idle(database)
        config = payload.config or database_graph_config(database)
        if config is None:
            raise KnowledgeError('KNOWLEDGE_GRAPH_MODEL_REQUIRED', '试构建前请选择对话模型')
        from plugins.ai.knowledge.stores import require_graph

        require_graph()
        resolved = await self._resolve(config.llm_model_id, 'chat')
        await self._check_embedding_spec(database)
        async with self.metadata.lock(db_id):
            state = await self._graph_state()
            database = self._database(state, db_id)
            if database.get('graph_preview', {}).get('status') in {'queued', 'running'}:
                return self._preview_result(database)
            self._ensure_graph_idle(database)
            if payload.document_id:
                document = self._document(state, db_id, payload.document_id)
            else:
                document = next(
                    (doc for doc in state['documents'].values() if doc['db_id'] == db_id and doc['status'] == 'done'),
                    None,
                )
            if not document or document['status'] != 'done' or not database.get('index_spec'):
                raise KnowledgeError('KNOWLEDGE_GRAPH_PREVIEW_EMPTY', '请先上传并完成至少一个文档的向量索引')
            previous = database.get('graph_preview')
            if previous and previous.get('cleanup_pending'):
                await self._cleanup_graph_preview(database, previous)
            job_id = uuid4().hex
            preview = {
                'status': 'queued',
                'job_id': job_id,
                'workspace': f'kb_{uuid4().hex}',
                'document_id': document['id'],
                'probe_document_id': f'doc_{uuid4().hex}',
                'file_name': document['file_name'],
                'config': config.model_dump(),
                'model_snapshot': model_snapshot(resolved),
                'result': None,
                'error': None,
                'progress': None,
                'started_at': None,
                'finished_at': None,
                'cleanup_pending': True,
            }
            async with self.metadata.transaction() as current:
                self._database(current, db_id)['graph_preview'] = preview
            task = asyncio.create_task(self._run_graph_preview(db_id, job_id), name=f'graph-preview-{job_id}')
            self._register_graph_task(job_id, task)
            return self._preview_result({'graph_preview': preview})

    async def _cleanup_graph_preview(self, database: dict, preview: dict) -> None:
        view = dict(
            database,
            enable_knowledge_graph=True,
            llm_model_id=preview['config']['llm_model_id'],
            _graph_workspace=preview['workspace'],
            _graph_config=preview['config'],
            _graph_snapshot=preview['model_snapshot'],
        )
        graph = await self._graph(view)
        await graph.delete_database([preview['probe_document_id']])
        await graph.close()
        self._graphs.pop(preview['workspace'], None)

    async def _run_graph_preview(self, db_id: str, job_id: str) -> None:
        from module_Knowledge.src.knowledge.indexing import process_file_to_markdown
        from plugins.ai.knowledge.stores import _wait_for_completion

        started = time.monotonic()
        result, error, status = None, None, 'failed'
        database, preview = None, None
        try:
            async with self.metadata.transaction() as state:
                database = self._database(state, db_id)
                candidate = database.get('graph_preview')
                if not candidate or candidate['job_id'] != job_id:
                    return
                preview = candidate
                preview.update(status='running', started_at=datetime.now(timezone.utc).isoformat())
                document = self._document(state, db_id, preview['document_id'])
                upload = state['uploads'][document['upload_id']]
            view = dict(
                database,
                enable_knowledge_graph=True,
                llm_model_id=preview['config']['llm_model_id'],
                _graph_workspace=preview['workspace'],
                _graph_config=preview['config'],
                _graph_snapshot=preview['model_snapshot'],
            )
            graph = await self._graph(view)
            content = await asyncio.to_thread(process_file_to_markdown, self._upload_path(upload))
            result = await graph.preview(preview['probe_document_id'], content, preview['file_name'])
            result['seconds'] = round(time.monotonic() - started, 2)
            status = 'completed'
        except asyncio.CancelledError:
            status = 'interrupted'
            error = self._graph_error('KNOWLEDGE_GRAPH_INTERRUPTED')
            raise
        except Exception as failure:
            known = safe_graph_error(failure)
            error = known.public_dict() if known else self._graph_error()
        finally:
            if database is not None and preview is not None:
                cleanup = asyncio.create_task(self._cleanup_graph_preview(database, preview))
                await _wait_for_completion(cleanup)
                cleaned = not cleanup.cancelled() and cleanup.exception() is None
                if not cleaned:
                    status, result = 'failed', None
                    error = self._graph_error('KNOWLEDGE_GRAPH_CLEANUP_FAILED')
                async with self.metadata.transaction() as state:
                    current = state['databases'].get(db_id, {}).get('graph_preview')
                    if current and current['job_id'] == job_id:
                        current.update(
                            status=status,
                            result=result,
                            error=error,
                            cleanup_pending=not cleaned,
                            finished_at=datetime.now(timezone.utc).isoformat(),
                        )
