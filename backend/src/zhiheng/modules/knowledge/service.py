"""Knowledge ownership, metadata and immutable build admission facts."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.public import AclEntry, BuiltinPermission, FileInfo, FileService, SourceSnapshot
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.contracts import (
    JOB_TERMINAL,
    NATIVE_FORMATS,
    PREPARATION_TERMINAL,
    AttachmentImportInfo,
    BaseAccess,
    BaseAcl,
    BaseAclInfo,
    BaseCreate,
    BaseInfo,
    BasePatch,
    DocumentInfo,
    DocumentPage,
    DocumentResult,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    JobInfo,
    KnowledgeSettings,
    MigrationRequest,
    UnavailableBaseInfo,
)
from zhiheng.modules.knowledge.graph.lifecycle import retire_base_graph
from zhiheng.modules.knowledge.graph.orm import GraphConfiguration, GraphGeneration, GraphJob
from zhiheng.modules.knowledge.orm import (
    IndexGeneration,
    KnowledgeBase,
    KnowledgeDefaults,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeGrant,
    KnowledgeJob,
    KnowledgeMaterial,
    KnowledgeMigration,
    KnowledgeUpload,
    KnowledgeUploadReceipt,
    RetrievalIntent,
)
from zhiheng.modules.knowledge.parsing_orm import ParsingJob
from zhiheng.modules.models.public import ModelBinding, ModelService
from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import ResourceReference
from zhiheng.shared.time import utcnow


@dataclass(frozen=True)
class BuildSelection:
    file_ids: list[UUID] | None = None
    document_ids: list[UUID] | None = None
    folder_id: UUID | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    confirm_references: bool = False

    def __post_init__(self) -> None:
        if self.file_ids is not None and len(set(self.file_ids)) != len(self.file_ids):
            raise AppError('invalid_source_selection', '入库源文件不能重复', 422)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def binding_fingerprint(binding: ModelBinding) -> str:
    return fingerprint(binding.model_dump(mode='json', exclude={'max_output_tokens', 'vision_enabled'}))


PreparationSkillOwner = Callable[[AsyncSession, str, UUID, dict[str, object]], Awaitable[UUID | None]]


class KnowledgeService:
    def __init__(
        self,
        files: FileService,
        identity: IdentityService,
        models: ModelService,
        *,
        preparation_skill_owner: PreparationSkillOwner | None = None,
        base_references: Callable[[AsyncSession, Principal, UUID], Awaitable[dict[str, object]]] | None = None,
    ) -> None:
        self.files, self.identity, self.models = files, identity, models
        self.preparation_skill_owner = preparation_skill_owner
        self.base_references = base_references

    async def required_rerank_id(
        self, session: AsyncSession, *, use_default: bool = True, model_id: UUID | None = None
    ) -> UUID:
        if use_default:
            defaults = await session.get(KnowledgeDefaults, 1)
            model_id = defaults.default_rerank_model_id if defaults is not None else None
        if model_id is None:
            raise AppError('knowledge_rerank_required', '必须配置重排模型，请选择模型或联系管理员设置默认重排', 409)
        return model_id

    async def _rerank_binding(self, session: AsyncSession, model_id: UUID) -> ModelBinding:
        try:
            binding = await self.models.resolve(session, model_id, lock=False)
        except AppError as error:
            raise AppError('knowledge_rerank_unavailable', '重排模型不可用，请检查启用和连接状态', 409) from error
        if binding.model_type != 'rerank':
            raise AppError('invalid_knowledge_model', '请选择已启用的重排模型', 422)
        return binding

    async def default_rerank_binding(self, session: AsyncSession) -> ModelBinding:
        return await self._rerank_binding(session, await self.required_rerank_id(session))

    async def retrieval_rerank_binding(self, session: AsyncSession, base_ids: list[UUID]) -> ModelBinding:
        bases = list(await session.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(base_ids))))
        choices = {(base.use_default_rerank, base.rerank_model_id) for base in bases}
        if len(choices) != 1 or next(iter(choices))[0]:
            return await self.default_rerank_binding(session)
        model_id = await self.required_rerank_id(session, use_default=False, model_id=next(iter(choices))[1])
        return await self._rerank_binding(session, model_id)

    async def defaults_ready(self, session: AsyncSession) -> bool:
        defaults = await session.get(KnowledgeDefaults, 1)
        if defaults is None or defaults.default_embed_model_id is None:
            return False
        try:
            binding = await self.models.resolve(session, defaults.default_embed_model_id, lock=False)
            return binding.model_type == 'embedding'
        except AppError:
            return False

    async def migration_preview(self, session: AsyncSession, principal: Principal, base_id: UUID) -> dict[str, object]:
        base = await self.require(session, principal, base_id, manage=True)
        grants = list(await session.scalars(select(KnowledgeGrant).where(KnowledgeGrant.base_id == base_id)))
        cleanup = any(grant.effect != 'allow' or grant.expires_at is not None for grant in grants)
        documents = []
        rows = await session.scalars(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocument.migration_source_document_id.is_(None),
            )
            .order_by(KnowledgeDocument.created_at)
        )
        for document in rows:
            reason = None
            try:
                file = await self.files.require(session, principal, document.file_id, active=True)
                if file.owner_id != principal.id or file.source_file_id is not None:
                    reason = '资料原文不归知识库所有者所有，请先处理'
                elif file.original_filename.rpartition('.')[2].lower() not in NATIVE_FORMATS:
                    reason = '资料格式无法处理，请先移除或替换'
            except AppError:
                reason = '资料原文缺失或无读取权限，请先处理'
            documents.append(
                {
                    'document_id': str(document.id),
                    'file_name': document.file_name,
                    'can_migrate': reason is None,
                    'reason': reason,
                }
            )
        return {
            'base_id': str(base.id),
            'document_policy': base.document_policy,
            'expected_version': base.version,
            'acl_version': base.acl_version,
            'sharing': {
                'access': base.access,
                'entries': [
                    {
                        'user_id': str(grant.user_id),
                        'effect': grant.effect,
                        'expires_at': grant.expires_at.isoformat() if grant.expires_at else None,
                    }
                    for grant in grants
                ],
            },
            'documents': documents,
            'requires_acl_cleanup': cleanup,
            'can_migrate': base.document_policy == 'legacy'
            and not cleanup
            and all(item['can_migrate'] for item in documents),
        }

    async def migration_sources(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: MigrationRequest
    ) -> list[DocumentInfo]:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.version(base.version, body.expected_version)
        self.version(base.acl_version, body.expected_acl_version)
        self.idle(base)
        if not body.confirm_shared_access:
            raise AppError('migration_confirmation_required', '请确认迁移后共享用户可查看和下载全部资料', 422)
        preview = await self.migration_preview(session, principal, base_id)
        if not preview['can_migrate']:
            raise AppError(
                'knowledge_migration_blocked', '请先处理资料所有权、缺失文件或历史共享限制', 409, details=preview
            )
        return [
            await self.document_info(session, base_id, UUID(str(item['document_id'])))
            for item in cast('list[dict[str, object]]', preview['documents'])
        ]

    async def migration_copy(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        source: DocumentInfo,
        file: FileInfo,
    ) -> UUID:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        if base.document_policy != 'legacy':
            raise AppError('knowledge_migration_blocked', '知识库资料权限已变化', 409)
        old = await self.document(session, base_id, source.id)
        row = KnowledgeDocument(
            base_id=base_id,
            file_id=file.id,
            folder_id=old.folder_id,
            migration_source_document_id=old.id,
            file_name=file.original_filename,
            file_size=file.size,
            mime=file.mime,
            source_sha256=file.sha256,
            chunk_size=old.chunk_size,
            chunk_overlap=old.chunk_overlap,
        )
        session.add(row)
        await session.flush()
        session.add(KnowledgeMaterial(base_id=base_id, file_id=file.id, document_id=row.id))
        await session.flush()
        snapshot = await self.files.source_snapshot(session, principal, file.id)
        await self.files.bind_sources(
            session, principal, (snapshot,), resource_type='knowledge_document', resource_id=row.id
        )
        return row.id

    async def existing_migration_copy(
        self, session: AsyncSession, base_id: UUID, source: DocumentInfo, source_sha256: str
    ) -> UUID | None:
        return cast(
            'UUID | None',
            await session.scalar(
                select(KnowledgeDocument.id)
                .where(
                    KnowledgeDocument.base_id == base_id,
                    KnowledgeDocument.migration_source_document_id == source.id,
                    KnowledgeDocument.source_sha256 == source_sha256,
                    KnowledgeDocument.deleted_at.is_(None),
                )
                .order_by(KnowledgeDocument.created_at.desc())
                .limit(1)
            ),
        )

    async def queue_migration(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: MigrationRequest,
        document_ids: list[UUID],
        sources: list[dict[str, object]],
    ) -> JobInfo:
        await self.migration_sources(session, principal, base_id, body)
        base = await self.require(session, principal, base_id, manage=True)
        binding = await self.models.resolve(session, base.embed_model_id)
        job = await self.queue(
            session,
            principal,
            base_id,
            operation='rebuild',
            request_id=body.client_request_id,
            value=fingerprint(['migration', str(base_id), body.model_dump(mode='json')]),
            binding=binding,
            selection=BuildSelection(document_ids=document_ids),
        )
        session.add(KnowledgeMigration(base_id=base_id, job_id=job.id, acl_version=base.acl_version, documents=sources))
        await session.flush()
        return job

    async def validate_migration(self, session: AsyncSession, job_id: UUID, *, lock: bool = False) -> None:
        migration = await session.scalar(select(KnowledgeMigration).where(KnowledgeMigration.job_id == job_id))
        if migration is None or migration.status != 'pending':
            return
        base_query = select(KnowledgeBase).where(KnowledgeBase.id == migration.base_id)
        base = await session.scalar(base_query)
        if lock and base is not None:
            owner = await self.identity.principal_for_user(session, base.owner_id)
            await self.lock_library_sources(
                session,
                owner,
                tuple(UUID(str(item['source_file_id'])) for item in migration.documents),
                additional_base_ids=(base.id,),
            )
            base = await session.scalar(base_query.with_for_update().execution_options(populate_existing=True))
            # Publish follows the same all bases -> job -> all sources order as worker admission.
            await session.execute(select(KnowledgeJob.id).where(KnowledgeJob.id == job_id).with_for_update())
        if (
            base is None
            or base.deleted_at is not None
            or base.document_policy != 'legacy'
            or base.acl_version != migration.acl_version
        ):
            raise AppError('knowledge_migration_stale', '迁移期间共享范围已变化，保留原资料权限', 409)
        owner = await self.identity.principal_for_user(session, base.owner_id)
        for item in sorted(migration.documents, key=lambda source: str(source['source_file_id'])):
            snapshot = await self.files.source_snapshot(session, owner, UUID(str(item['source_file_id'])), lock=lock)
            if snapshot.model_dump(mode='json') != item['source_snapshot']:
                raise AppError('knowledge_migration_stale', '迁移原文或授权已变化，保留原资料权限', 409)

    async def finish_migration(self, session: AsyncSession, job_id: UUID) -> None:
        migration = await session.scalar(select(KnowledgeMigration).where(KnowledgeMigration.job_id == job_id))
        if migration is None or migration.status != 'pending':
            return
        # Revalidate under locks in the very transaction that changes the effective document policy.
        await self.validate_migration(session, job_id, lock=True)
        job = await session.get(KnowledgeJob, job_id)
        if job is None or job.status != 'completed':
            raise AppError('knowledge_migration_incomplete', '迁移索引未全部完成，保留原资料权限', 409)
        base = await session.get(KnowledgeBase, migration.base_id)
        assert base is not None
        for item in migration.documents:
            old = await self.document(session, base.id, UUID(str(item['source_document_id'])))
            old.deleted_at, old.status, old.active_revision = utcnow(), 'deleted', None
            await self.files.release_resources(
                session, resource_type='knowledge_document', resource_ids=(old.id,), actor_id=base.owner_id
            )
        base.document_policy, base.acl_version = 'library', base.acl_version + 1
        migration.status = 'completed'

    async def creation_parameters(self, session: AsyncSession, body: BaseCreate) -> BaseCreate:
        if body.embed_model_id is not None:
            return body
        defaults = await session.get(KnowledgeDefaults, 1)
        if defaults is None or defaults.default_embed_model_id is None:
            raise AppError('knowledge_defaults_missing', '请管理员先配置默认知识库检索服务', 409)
        return body.model_copy(
            update={
                'embed_model_id': defaults.default_embed_model_id,
                'chunk_size': defaults.chunk_size,
                'chunk_overlap': defaults.chunk_overlap,
            }
        )

    async def lock_library_sources(
        self,
        session: AsyncSession,
        principal: Principal,
        file_ids: tuple[UUID, ...],
        *,
        additional_base_ids: tuple[UUID, ...] = (),
    ) -> None:
        bases = set(
            await session.scalars(select(KnowledgeMaterial.base_id).where(KnowledgeMaterial.file_id.in_(file_ids)))
        ) | set(additional_base_ids)
        for base_id in sorted(bases):
            await self.require(session, principal, base_id, lock=True)

    async def authorize_library_file(
        self, session: AsyncSession, principal: Principal, file_id: UUID, *, lock: bool = False
    ) -> bool:
        material = await session.scalar(select(KnowledgeMaterial).where(KnowledgeMaterial.file_id == file_id))
        if material is None:
            return False  # Legacy originals keep their existing effective ACL.
        base = await self.require(session, principal, material.base_id, lock=lock)
        document = await session.get(KnowledgeDocument, material.document_id) if material.document_id else None
        if material.document_id is not None and (document is None or document.deleted_at is not None):
            raise AppError('document_not_found', '资料已移除或无访问权限', 404)
        if (
            document is None
            and await session.scalar(
                select(KnowledgeUpload.id).where(KnowledgeUpload.file_id == file_id, KnowledgeUpload.base_id == base.id)
            )
            is None
        ):
            raise AppError('document_not_found', '资料上传已移除', 404)
        # Staged uploads and pending migration copies are private to the owner.
        if (document is None or base.document_policy != 'library') and base.owner_id != principal.id:
            raise AppError('document_not_found', '资料不存在', 404)
        return True

    async def require_document_source(
        self, session: AsyncSession, principal: Principal, base_id: UUID, document_id: UUID, *, lock: bool = False
    ) -> FileInfo:
        await self.require(session, principal, base_id, lock=lock)
        document = await self.document(session, base_id, document_id)
        return await self.files.require(session, principal, document.file_id, active=True, lock=lock)

    async def document_source_snapshot(
        self,
        session: AsyncSession,
        principal: Principal,
        document: KnowledgeDocument | DocumentInfo,
        *,
        lock: bool = False,
    ) -> SourceSnapshot:
        await self.require_document_source(session, principal, document.base_id, document.id, lock=lock)
        return await self.files.source_snapshot(
            session,
            principal,
            document.file_id,
            lock=lock,
            artifact_file_id=getattr(document, 'artifact_file_id', None),
        )

    async def references(self, session: AsyncSession, principal: Principal, base_id: UUID) -> dict[str, object]:
        await self.require(session, principal, base_id, manage=True)
        result = await self.base_references(session, principal, base_id) if self.base_references else {}
        references = cast('list[ResourceReference]', result.get('items', []))
        # Keep the service boundary defensive even if the dependency provider changes.
        visible = [item for item in references if item.owner_id is None or item.owner_id == principal.id]
        hidden_count = int(cast('int', result.get('hidden_count', 0))) + len(references) - len(visible)
        return {
            'items': [
                {'kind': item.kind, 'id': str(item.id), 'name': item.name, 'status': item.status, 'path': item.path}
                for item in visible
            ],
            'hidden_count': hidden_count,
            'total': len(visible) + hidden_count,
        }

    async def confirm_deletion(
        self, session: AsyncSession, principal: Principal, base_id: UUID, *, confirmed: bool
    ) -> None:
        base = await self.require(session, principal, base_id, manage=True, include_deleted=True)
        if base.deleted_at is not None:
            return
        references = await self.references(session, principal, base_id)
        if references['total'] and not confirmed:
            raise AppError(
                'knowledge_referenced', '删除将使关联资源无法再使用此知识库，请确认影响', 409, details=references
            )

    async def has_active_work(self, session: AsyncSession) -> bool:
        """Pending build/graph/preparation work can spawn model steps later."""
        for model, terminal in (
            (KnowledgeJob, JOB_TERMINAL),
            (GraphJob, JOB_TERMINAL),
            (RetrievalIntent, PREPARATION_TERMINAL),
        ):
            if await session.scalar(select(model.id).where(model.status.not_in(terminal)).limit(1)) is not None:
                return True
        return False

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return bool(await self.model_references(session, model_id))

    async def model_references(self, session: AsyncSession, model_id: UUID) -> list[ResourceReference]:
        bases = await session.scalars(
            select(KnowledgeBase)
            .outerjoin(GraphConfiguration, GraphConfiguration.base_id == KnowledgeBase.id)
            .where(
                KnowledgeBase.deleted_at.is_(None),
                or_(
                    KnowledgeBase.embed_model_id == model_id,
                    KnowledgeBase.rerank_model_id == model_id,
                    GraphConfiguration.llm_model_id == model_id,
                ),
            )
            .order_by(KnowledgeBase.id)
        )
        references = [
            ResourceReference(
                'knowledge', base.id, base.name, 'active', base.owner_id, f'/knowledge/{base.id}/documents'
            )
            for base in bases
        ]
        defaults = await session.get(KnowledgeDefaults, 1, populate_existing=True)
        if defaults is not None:
            for field, label in (
                ('default_embed_model_id', '知识库默认 Embedding 模型'),
                ('default_rerank_model_id', '知识库默认重排模型'),
            ):
                if getattr(defaults, field) == model_id:
                    references.append(
                        ResourceReference(
                            'knowledge_defaults', model_id, label, 'configured', path='/knowledge/settings'
                        )
                    )
        references.extend(await self._binding_references(session, 'model_id', model_id))
        return references

    async def connection_references(self, session: AsyncSession, connection_id: UUID) -> list[ResourceReference]:
        """Parents can still spawn a child using an older connection after model rebinding."""
        return await self._binding_references(session, 'connection_id', connection_id)

    async def _binding_references(
        self,
        session: AsyncSession,
        field: str,
        value: UUID,
    ) -> list[ResourceReference]:
        references: list[ResourceReference] = []
        jobs = await session.execute(
            select(KnowledgeJob, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeJob.base_id)
            .join(IndexGeneration, IndexGeneration.id == KnowledgeJob.index_revision)
            .where(KnowledgeJob.status.not_in(JOB_TERMINAL), IndexGeneration.binding[field].astext == str(value))
            .order_by(KnowledgeJob.id)
        )
        references.extend(
            ResourceReference(
                'knowledge', job.id, base.name, job.status, base.owner_id, f'/knowledge/{base.id}/documents'
            )
            for job, base in jobs
        )
        graph_jobs = await session.execute(
            select(GraphJob, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == GraphJob.base_id)
            .where(
                GraphJob.status.not_in(JOB_TERMINAL),
                GraphJob.operation != 'gc',
                or_(
                    GraphJob.frozen['llm_binding'][field].astext == str(value),
                    GraphJob.frozen['embedding_binding'][field].astext == str(value),
                ),
            )
            .order_by(GraphJob.id)
        )
        references.extend(
            ResourceReference('knowledge', job.id, base.name, job.status, base.owner_id, f'/knowledge/{base.id}/graph')
            for job, base in graph_jobs
        )
        preparations = await session.scalars(
            select(RetrievalIntent)
            .where(
                RetrievalIntent.status.not_in(PREPARATION_TERMINAL),
                or_(
                    RetrievalIntent.dependencies['main_request']['binding'][field].astext == str(value),
                    RetrievalIntent.dependencies['rerank_binding'][field].astext == str(value),
                    RetrievalIntent.dependencies['bases'].contains([{'binding': {field: str(value)}}]),
                ),
            )
            .order_by(RetrievalIntent.id)
        )
        references.extend(self._preparation_reference(row) for row in preparations)
        return references

    async def skill_references(
        self,
        session: AsyncSession,
        slug: str,
        *,
        owner_id: UUID,
        skill_ids: tuple[UUID, ...] = (),
    ) -> list[ResourceReference]:
        rows = await session.scalars(
            select(RetrievalIntent)
            .where(
                RetrievalIntent.status.not_in(PREPARATION_TERMINAL),
                or_(
                    RetrievalIntent.dependencies['config']['skill_slugs'].contains([slug]),
                    RetrievalIntent.dependencies['member_config']['skill_slugs'].contains([slug]),
                    RetrievalIntent.dependencies['agent_config']['skill_slugs'].contains([slug]),
                    RetrievalIntent.dependencies['member_skills'].contains([{'slug': slug}]),
                    RetrievalIntent.request['skill_slugs'].contains([slug]),
                    *(
                        RetrievalIntent.dependencies['member_skills'].contains([{'skill_id': str(skill_id)}])
                        for skill_id in skill_ids
                    ),
                ),
            )
            .order_by(RetrievalIntent.id)
        )
        references = []
        for row in rows:
            if row.resource_type in {'work_turn', 'agent_preview', 'agent_invocation'}:
                if self.preparation_skill_owner is None:
                    continue
                skill_owner = await self.preparation_skill_owner(
                    session, row.resource_type, row.resource_id, row.request
                )
            else:
                skill_owner = row.owner_id
            if skill_owner == owner_id:
                references.append(self._preparation_reference(row))
        return references

    async def agent_references(self, session: AsyncSession, agent_id: UUID) -> list[ResourceReference]:
        rows = await session.scalars(
            select(RetrievalIntent)
            .where(
                RetrievalIntent.status.not_in(PREPARATION_TERMINAL),
                or_(
                    RetrievalIntent.resource_type.in_(('agent_preview', 'agent_invocation'))
                    & (RetrievalIntent.resource_id == agent_id),
                    (RetrievalIntent.resource_type == 'work_turn')
                    & (RetrievalIntent.request['body']['agent_id'].astext == str(agent_id)),
                ),
            )
            .order_by(RetrievalIntent.id)
        )
        return [self._preparation_reference(row) for row in rows]

    @staticmethod
    def _preparation_reference(row: RetrievalIntent) -> ResourceReference:
        path = None
        if row.resource_type in {'agent_preview', 'agent_invocation'}:
            path = f'/agents/{row.resource_id}'
        elif row.resource_type == 'work_turn':
            path = f'/work?taskId={row.resource_id}'
        elif row.resource_type == 'knowledge_query':
            path = f'/knowledge/{row.resource_id}/query'
        return ResourceReference('knowledge', row.id, '知识检索准备任务', row.status, row.owner_id, path)

    async def deletion_binding(self, session: AsyncSession, base_id: UUID) -> ModelBinding:
        base = await session.get(KnowledgeBase, base_id)
        assert base is not None
        retained = await self.models.retained_binding(session, base.embed_model_id)
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        return ModelBinding.model_validate(active.binding) if active is not None else retained

    @staticmethod
    def manages(principal: Principal, base: KnowledgeBase) -> bool:
        return principal.id == base.owner_id

    async def _related_base(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        manage: bool = False,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> KnowledgeBase:
        query = select(KnowledgeBase).where(KnowledgeBase.id == base_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        base = await session.scalar(query)
        if base is None or (base.deleted_at is not None and not include_deleted):
            raise AppError('knowledge_not_found', '知识库不存在', 404)
        allowed = self.manages(principal, base)
        if not allowed and not manage:
            grant = await session.get(KnowledgeGrant, (base.id, principal.id), populate_existing=lock)
            effective = grant is not None and (grant.expires_at is None or grant.expires_at > utcnow())
            allowed = bool(effective and grant and grant.effect == 'allow')
        if not allowed:
            raise AppError('knowledge_not_found', '知识库不存在', 404)
        return base

    async def require(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        manage: bool = False,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> KnowledgeBase:
        base = await self._related_base(
            session, principal, base_id, manage=manage, lock=lock, include_deleted=include_deleted
        )
        owner = await self.identity.resource_owner(session, base.owner_id, lock=lock)
        if owner.state != 'active':
            raise AppError(
                'knowledge_owner_unavailable',
                '创建者已删除' if owner.state == 'deleted' else '创建者已停用',
                404,
                details={'availability': 'owner_deleted' if owner.state == 'deleted' else 'owner_disabled'},
            )
        return base

    async def authorize_base(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        manage: bool = False,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> BaseAccess:
        base = await self.require(
            session, principal, base_id, manage=manage, lock=lock, include_deleted=include_deleted
        )
        return BaseAccess(base.id, base.owner_id, base.embed_model_id, base.version, base.active_job_id)

    async def delegable_access(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> tuple[BaseAccess, frozenset[str]]:
        base = await self.authorize_base(session, principal, base_id, lock=lock, include_deleted=include_deleted)
        if base.owner_id == principal.id:
            return base, frozenset({'read', 'write', 'query', 'delete'})
        if include_deleted:
            await self.require(session, principal, base_id)
        grant = await session.get(KnowledgeGrant, (base.id, principal.id), populate_existing=lock)
        if grant is None or grant.effect != 'allow' or (grant.expires_at is not None and grant.expires_at <= utcnow()):
            raise AppError('knowledge_not_found', '知识库不存在', 404)
        return base, frozenset({'read', 'query'})

    async def delegable_metadata(self, session: AsyncSession, principal: Principal) -> list[BaseInfo]:
        now = utcnow()
        shared = select(KnowledgeGrant.base_id).where(
            KnowledgeGrant.user_id == principal.id,
            KnowledgeGrant.effect == 'allow',
            or_(KnowledgeGrant.expires_at.is_(None), KnowledgeGrant.expires_at > now),
        )
        rows = await session.scalars(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.deleted_at.is_(None),
                or_(KnowledgeBase.owner_id == principal.id, KnowledgeBase.id.in_(shared)),
            )
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id)
        )
        result = []
        for row in rows:
            try:
                await self.delegable_access(session, principal, row.id)
                result.append(await self.info(session, principal, row))
            except AppError as error:  # noqa: PERF203 -- each current ACL must be checked before exposing metadata
                if error.status_code != HTTPStatus.NOT_FOUND:
                    raise
        return result

    async def detail(self, session: AsyncSession, principal: Principal, base_id: UUID) -> BaseInfo:
        return await self.info(session, principal, await self.require(session, principal, base_id))

    async def info(self, session: AsyncSession, principal: Principal, base: KnowledgeBase) -> BaseInfo:
        await self.require(session, principal, base.id)
        count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(KnowledgeDocument.base_id == base.id, KnowledgeDocument.deleted_at.is_(None))
        )
        status = (
            'building'
            if base.active_job_id
            else 'ready'
            if base.active_revision
            else 'failed'
            if base.last_error
            else 'empty'
        )
        if base.active_revision and count:
            generation = await session.get(IndexGeneration, base.active_revision)
            try:
                binding = await self.models.resolve(session, base.embed_model_id, lock=False)
                if generation is None or binding_fingerprint(binding) != generation.fingerprint:
                    status = 'reindex_required'
            except AppError:
                status = 'reindex_required'
        graph_config = await session.get(GraphConfiguration, base.id)
        graph_status = 'disabled'
        if graph_config is not None:
            running = await session.scalar(
                select(GraphJob.id)
                .where(
                    GraphJob.base_id == base.id,
                    GraphJob.operation.in_(('build', 'preview')),
                    GraphJob.status.in_(('pending', 'running', 'waiting_model', 'draining')),
                )
                .limit(1)
            )
            graph_status = 'building' if running else 'pending'
            if graph_config.active_revision is not None:
                graph_generation = await session.get(GraphGeneration, graph_config.active_revision)
                graph_status = 'ready' if graph_generation and graph_generation.status == 'active' else 'stale'
                if graph_generation:
                    frozen = graph_generation.frozen
                    configured = frozen.get('config')
                    if (
                        frozen.get('index_revision') != str(base.active_revision)
                        or not isinstance(configured, dict)
                        or configured.get('version') != graph_config.version
                    ):
                        graph_status = 'stale'
            elif await session.scalar(
                select(GraphJob.id).where(GraphJob.base_id == base.id, GraphJob.status == 'failed').limit(1)
            ):
                graph_status = 'failed'
            if running:
                graph_status = 'building'
        return BaseInfo(
            id=base.id,
            document_policy=cast("Literal['legacy', 'library']", base.document_policy),
            sharing_scope='users'
            if await session.scalar(
                select(KnowledgeGrant.user_id)
                .where(KnowledgeGrant.base_id == base.id, KnowledgeGrant.effect == 'allow')
                .limit(1)
            )
            else 'private',
            owner_name=(await self.identity.resource_owner(session, base.owner_id)).username,
            name=base.name,
            description=base.description,
            owner_id=base.owner_id,
            access='private',
            version=base.version,
            acl_version=base.acl_version,
            embed_model_id=base.embed_model_id,
            rerank_model_id=base.rerank_model_id,
            use_default_rerank=base.use_default_rerank,
            chunk_size=base.chunk_size,
            chunk_overlap=base.chunk_overlap,
            active_revision=base.active_revision,
            active_job_id=base.active_job_id,
            index_status=status,
            document_count=count or 0,
            last_error=RunError.model_validate(base.last_error) if base.last_error else None,
            enable_knowledge_graph=graph_config is not None,
            llm_model_id=graph_config.llm_model_id if graph_config else None,
            graph_status=cast("Literal['disabled','pending','ready','building','stale','failed']", graph_status),
            created_at=base.created_at,
            updated_at=base.updated_at,
            can_manage=self.manages(principal, base),
        )

    async def list_bases(
        self, session: AsyncSession, principal: Principal, *, available: bool = False, scope: str = 'all'
    ) -> list[BaseInfo | UnavailableBaseInfo]:
        result: list[BaseInfo | UnavailableBaseInfo] = []
        query = select(KnowledgeBase).where(KnowledgeBase.deleted_at.is_(None))
        if not available and scope == 'owned':
            query = query.where(KnowledgeBase.owner_id == principal.id)
        elif not available and scope == 'shared':
            query = query.where(KnowledgeBase.owner_id != principal.id)
        for base in await session.scalars(query.order_by(KnowledgeBase.created_at.desc())):
            try:
                await self._related_base(session, principal, base.id)
            except AppError as error:
                if error.status_code != HTTPStatus.NOT_FOUND:
                    raise
                continue
            owner = await self.identity.resource_owner(session, base.owner_id)
            if owner.state != 'active':
                if not available:
                    result.append(
                        UnavailableBaseInfo(
                            id=base.id,
                            name=base.name,
                            owner_id=base.owner_id,
                            owner_name=owner.username,
                            availability='owner_deleted' if owner.state == 'deleted' else 'owner_disabled',
                        )
                    )
                continue
            try:
                info = await self.info(session, principal, base)
            except AppError as error:
                if error.code != 'knowledge_owner_unavailable':
                    raise
                # Account state may have changed between metadata and content checks.
                if not available:
                    owner = await self.identity.resource_owner(session, base.owner_id)
                    result.append(
                        UnavailableBaseInfo(
                            id=base.id,
                            name=base.name,
                            owner_id=base.owner_id,
                            owner_name=owner.username,
                            availability='owner_deleted' if owner.state == 'deleted' else 'owner_disabled',
                        )
                    )
                continue
            if not available or (base.active_revision is not None and info.index_status != 'reindex_required'):
                result.append(info)
        return result

    async def create(
        self, session: AsyncSession, principal: Principal, body: BaseCreate, binding: ModelBinding
    ) -> BaseInfo:
        if binding.model_type != 'embedding':
            raise AppError('embedding_model_required', '请选择Embedding模型', 422)
        if body.llm_model_id is not None and not body.enable_knowledge_graph:
            raise AppError('graph_model_without_graph', '请先启用图谱功能', 422)
        if body.enable_knowledge_graph and body.llm_model_id is None:
            raise AppError('graph_model_required', '请选择图谱抽取模型', 422)
        if body.llm_model_id:
            graph_binding = await self.models.resolve(session, body.llm_model_id)
            if graph_binding.model_type != 'chat':
                raise AppError('graph_chat_required', '图谱需要聊天模型', 422)
        await self.default_rerank_binding(session)
        row = KnowledgeBase(
            owner_id=principal.id,
            document_policy='library',
            **body.model_dump(exclude={'enable_knowledge_graph', 'llm_model_id'}),
        )
        session.add(row)
        await session.flush()
        if body.enable_knowledge_graph and body.llm_model_id:
            session.add(GraphConfiguration(base_id=row.id, llm_model_id=body.llm_model_id))
            await session.flush()
        return await self.info(session, principal, row)

    @staticmethod
    def version(actual: int, expected: int) -> None:
        if actual != expected:
            raise AppError('knowledge_version_conflict', '知识资源已更新，请刷新后重试', 409)

    @staticmethod
    def idle(base: KnowledgeBase | BaseAccess) -> None:
        if base.active_job_id is not None:
            raise AppError('knowledge_busy', '知识库仍有索引修改任务', 409)

    async def patch(self, session: AsyncSession, principal: Principal, base_id: UUID, body: BasePatch) -> BaseInfo:
        row = await self.require(session, principal, base_id, manage=True, lock=True)
        self.version(row.version, body.expected_version)
        self.idle(row)
        for key, value in body.model_dump(exclude={'expected_version'}, exclude_none=True).items():
            setattr(row, key, value)
        row.version += 1
        row.updated_at = utcnow()
        return await self.info(session, principal, row)

    async def configure(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: KnowledgeSettings,
        binding: ModelBinding,
        value: str,
    ) -> tuple[BaseInfo, JobInfo | None]:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.version(base.version, body.expected_version)
        self.idle(base)
        ids = await self.all_document_ids(session, base_id)
        structural = (base.embed_model_id, base.chunk_size, base.chunk_overlap) != (
            body.embed_model_id,
            body.chunk_size,
            body.chunk_overlap,
        )
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        stale = active is not None and active.fingerprint != binding_fingerprint(binding)
        rebuild = bool(ids) and (structural or stale)
        if rebuild and not body.confirm_rebuild:
            raise AppError('knowledge_rebuild_confirmation_required', '更换向量模型或切片策略需要确认重新处理资料', 409)
        if body.name is not None:
            base.name = body.name
        if body.description is not None:
            base.description = body.description
        base.rerank_model_id = body.rerank_model_id if not body.use_default_rerank else None
        base.use_default_rerank = body.use_default_rerank
        base.chunk_size, base.chunk_overlap = body.chunk_size, body.chunk_overlap
        base.version, base.updated_at = base.version + 1, utcnow()
        job = None
        if rebuild:
            job = await self.queue(
                session,
                principal,
                base_id,
                operation='rebuild',
                request_id=body.client_request_id,
                value=value,
                binding=binding,
                selection=BuildSelection(document_ids=ids),
            )
        elif not ids:
            if active is not None:
                active.status, active.gc_after = 'retired', utcnow()
                base.active_revision = None
            base.embed_model_id, base.last_error = body.embed_model_id, None
        await session.flush()
        return await self.info(session, principal, base), job

    async def acl(self, session: AsyncSession, principal: Principal, base_id: UUID) -> BaseAclInfo:
        row = await self.require(session, principal, base_id, manage=True)
        entries = [
            AclEntry(user_id=g.user_id, effect=cast("Literal['allow','deny']", g.effect), expires_at=g.expires_at)
            for g in await session.scalars(select(KnowledgeGrant).where(KnowledgeGrant.base_id == row.id))
        ]
        return BaseAclInfo(
            base=await self.info(session, principal, row),
            entries=entries,
            builtin_permissions=[
                BuiltinPermission(source=source, enabled=True, deny_overridable=False) for source in ('owner',)
            ],
        )

    async def set_acl(self, session: AsyncSession, principal: Principal, base_id: UUID, body: BaseAcl) -> BaseAclInfo:
        row = await self.require(session, principal, base_id, manage=True, lock=True)
        self.version(row.acl_version, body.expected_version)
        if row.document_policy == 'library' and any(
            entry.effect != 'allow' or entry.expires_at is not None for entry in body.entries
        ):
            raise AppError('invalid_library_sharing', '知识库共享仅支持无到期时间的只读用户', 422)
        if len({entry.user_id for entry in body.entries}) != len(body.entries):
            raise AppError('invalid_acl', '授权用户不能重复', 422)
        for entry in body.entries:
            if not await self.identity.is_active(session, entry.user_id):
                raise AppError('invalid_acl', '授权用户不可用', 422)
        await session.execute(delete(KnowledgeGrant).where(KnowledgeGrant.base_id == row.id))
        session.add_all([KnowledgeGrant(base_id=row.id, **entry.model_dump()) for entry in body.entries])
        row.access, row.acl_version, row.updated_at = body.access, row.acl_version + 1, utcnow()
        row.invite_epoch += 1
        await session.flush()
        return await self.acl(session, principal, base_id)

    async def folder(
        self, session: AsyncSession, base_id: UUID, folder_id: UUID | None, *, include_deleted: bool = False
    ) -> KnowledgeFolder | None:
        if folder_id is None:
            return None
        query = select(KnowledgeFolder).where(KnowledgeFolder.id == folder_id, KnowledgeFolder.base_id == base_id)
        if not include_deleted:
            query = query.where(KnowledgeFolder.deleted_at.is_(None))
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise AppError('folder_not_found', '文件夹不存在', 404)
        return row

    async def folders(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[FolderInfo]:
        await self.require(session, principal, base_id)
        return [
            FolderInfo.model_validate(row)
            for row in await session.scalars(
                select(KnowledgeFolder)
                .where(KnowledgeFolder.base_id == base_id, KnowledgeFolder.deleted_at.is_(None))
                .order_by(KnowledgeFolder.created_at)
            )
        ]

    async def create_folder(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: FolderCreate
    ) -> FolderInfo:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        await self.folder(session, base_id, body.parent_id)
        row = KnowledgeFolder(base_id=base_id, **body.model_dump())
        session.add(row)
        await session.flush()
        return FolderInfo.model_validate(row)

    async def authorize_folder(
        self, session: AsyncSession, base_id: UUID, folder_id: UUID | None, *, include_deleted: bool = False
    ) -> FolderInfo | None:
        folder = await self.folder(session, base_id, folder_id, include_deleted=include_deleted)
        return FolderInfo.model_validate(folder) if folder is not None else None

    async def rename_folder(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID, body: FolderPatch
    ) -> FolderInfo:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        folder = await self.folder(session, base_id, folder_id)
        assert folder is not None
        self.version(folder.version, body.expected_version)
        folder.name, folder.version, folder.updated_at = body.name, folder.version + 1, utcnow()
        return FolderInfo.model_validate(folder)

    async def validate_import_policy(self, session: AsyncSession, base: KnowledgeBase, file_ids: list[UUID]) -> None:
        if base.document_policy != 'legacy' or not file_ids:
            return
        roots = await self.files.source_root_ids(session, tuple(file_ids))
        material = await session.scalar(
            select(KnowledgeMaterial.file_id).where(KnowledgeMaterial.file_id.in_(roots)).limit(1)
        )
        if material is not None:
            raise AppError('knowledge_migration_required', '请先迁移此知识库，再添加知识库资料副本', 409)

    async def import_sources(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        file_ids: list[UUID],
        folder_id: UUID | None,
    ) -> list[FileInfo]:
        base = await self.require(session, principal, base_id, manage=True)
        self.idle(base)
        await self.folder(session, base_id, folder_id)
        await self.validate_import_policy(session, base, file_ids)
        if base.document_policy != 'library':
            return []
        copies = []
        for file_id in file_ids:
            material = await session.get(KnowledgeMaterial, file_id)
            if material is not None and material.base_id == base_id and material.document_id is not None:
                await self.require_document_source(session, principal, base_id, material.document_id)
                continue
            uploaded = await session.scalar(select(KnowledgeUpload).where(KnowledgeUpload.file_id == file_id))
            if uploaded is not None:
                if uploaded.base_id != base_id or uploaded.folder_id != folder_id:
                    raise AppError('invalid_source', '上传不属于此知识库或文件夹', 422)
                continue
            file = await self.files.require(session, principal, file_id, active=True)
            if file.owner_id != principal.id or file.source_file_id is not None:
                raise AppError('attachment_not_owned', '只能加入自己拥有的原文件', 404)
            if file.original_filename.rpartition('.')[2].lower() not in NATIVE_FORMATS:
                raise AppError('unsupported_knowledge_format', '该格式不能加入知识库', 422)
            copies.append(file)
        return copies

    async def copied_source_file(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        source_id: UUID,
        folder_id: UUID | None,
    ) -> UUID | None:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        document = await session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.origin_file_id == source_id,
                KnowledgeDocument.deleted_at.is_(None),
            )
            .limit(1)
        )
        if document is not None:
            return document.file_id
        self.idle(base)
        upload = await session.scalar(
            select(KnowledgeUpload)
            .where(
                KnowledgeUpload.base_id == base_id,
                KnowledgeUpload.origin_file_id == source_id,
                KnowledgeUpload.folder_id == folder_id,
            )
            .limit(1)
        )
        return upload.file_id if upload else None

    async def imported_files_job(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file_ids: list[UUID]
    ) -> JobInfo | None:
        await self.require(session, principal, base_id, manage=True)
        if len(file_ids) != 1:
            return None
        material = await session.get(KnowledgeMaterial, file_ids[0])
        if material is None or material.base_id != base_id or material.document_id is None:
            return None
        document = await self.document(session, base_id, material.document_id)
        if document.job_id is None:
            return None
        job = await session.get(KnowledgeJob, document.job_id)
        return self.job_info(job) if job else None

    async def register_copy_upload(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        source: FileInfo,
        file: FileInfo,
        folder_id: UUID | None,
    ) -> None:
        await self.files.require(session, principal, source.id, active=True, lock=True)
        upload_id = await self.register_upload(session, principal, base_id, file, folder_id)
        upload = await session.get(KnowledgeUpload, upload_id)
        assert upload is not None
        upload.origin_file_id = source.id

    async def existing_attachment(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file_id: UUID
    ) -> AttachmentImportInfo | None:
        await self.require(session, principal, base_id, manage=True, lock=True)
        row = await session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.origin_file_id == file_id,
                KnowledgeDocument.deleted_at.is_(None),
            )
            .order_by(KnowledgeDocument.created_at)
            .limit(1)
        )
        if row is None or row.job_id is None:
            return None
        job = await session.get(KnowledgeJob, row.job_id)
        assert job is not None
        return AttachmentImportInfo(
            document=await self.document_info(session, base_id, row.id), job=self.job_info(job), already_exists=True
        )

    async def upload_receipt(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        request_id: UUID,
        value: str,
    ) -> dict[str, object] | None:
        await self.require(session, principal, base_id, manage=True)
        receipt = await session.get(KnowledgeUploadReceipt, (principal.id, request_id))
        if receipt is None:
            return None
        if receipt.base_id != base_id or receipt.fingerprint != value:
            raise AppError('idempotency_conflict', '上传标识已用于其他文件或知识库', 409)
        if receipt.status in {'discarded', 'expired'}:
            raise AppError('knowledge_upload_expired', '此上传已取消或过期，请重新选择文件', 409)
        file = await self.files.require(session, principal, receipt.file_id, active=True)
        return {'file': file.model_dump(mode='json'), 'upload_id': receipt.upload_id, 'folder_id': receipt.folder_id}

    async def find_upload_receipt(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        request_id: UUID,
    ) -> dict[str, object]:
        await self.require(session, principal, base_id, manage=True)
        receipt = await session.get(KnowledgeUploadReceipt, (principal.id, request_id))
        if receipt is None or receipt.base_id != base_id:
            raise AppError('knowledge_upload_not_found', '上传尚未接纳，请重新选择原文件后重试', 404)
        if receipt.status in {'discarded', 'expired'}:
            raise AppError('knowledge_upload_expired', '此上传已取消或过期，请重新选择文件', 409)
        file = await self.files.require(session, principal, receipt.file_id, active=True)
        return {'file': file.model_dump(mode='json'), 'upload_id': receipt.upload_id, 'folder_id': receipt.folder_id}

    async def remember_upload(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        request_id: UUID,
        value: str,
        file_id: UUID,
        upload_id: UUID,
        folder_id: UUID | None,
    ) -> None:
        session.add(
            KnowledgeUploadReceipt(
                owner_id=principal.id,
                request_id=request_id,
                base_id=base_id,
                file_id=file_id,
                upload_id=upload_id,
                folder_id=folder_id,
                fingerprint=value,
            )
        )
        await session.flush()

    async def finish_upload(self, session: AsyncSession, upload_id: UUID, status: str) -> None:
        await session.execute(
            update(KnowledgeUploadReceipt).where(KnowledgeUploadReceipt.upload_id == upload_id).values(status=status)
        )

    async def expired_uploads(self, session: AsyncSession, before: datetime) -> list[tuple[UUID, UUID, UUID]]:
        return [
            tuple(row)
            for row in await session.execute(
                select(KnowledgeBase.id, KnowledgeBase.owner_id, KnowledgeUpload.id)
                .join(KnowledgeUpload, KnowledgeUpload.base_id == KnowledgeBase.id)
                .where(KnowledgeUpload.created_at < before, KnowledgeBase.active_job_id.is_(None))
                .order_by(KnowledgeUpload.created_at)
                .limit(50)
            )
        ]

    async def register_upload(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file: FileInfo, folder_id: UUID | None
    ) -> UUID:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        await self.folder(session, base_id, folder_id)
        row = KnowledgeUpload(base_id=base_id, file_id=file.id, folder_id=folder_id)
        session.add(row)
        await session.flush()
        if base.document_policy == 'library':
            session.add(KnowledgeMaterial(base_id=base.id, file_id=file.id))
            await session.flush()
        owner = await self.identity.principal_for_user(session, base.owner_id)
        snapshot = await self.files.source_snapshot(session, owner, file.id)
        await self.files.bind_sources(session, owner, (snapshot,), resource_type='knowledge_upload', resource_id=row.id)
        return row.id

    async def retire_material(self, session: AsyncSession, file_id: UUID, *, actor_id: UUID) -> None:
        if await session.get(KnowledgeMaterial, file_id) is not None:
            await self.files.retire_business_source(session, file_id, actor_id=actor_id)

    async def remove_upload(self, session: AsyncSession, principal: Principal, base_id: UUID, upload_id: UUID) -> None:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        upload = await session.scalar(
            select(KnowledgeUpload).where(KnowledgeUpload.id == upload_id, KnowledgeUpload.base_id == base_id)
        )
        if upload is None:
            raise AppError('knowledge_upload_not_found', '暂存上传不存在', 404)
        source = await self.files.require(session, principal, upload.file_id)
        if source.purpose == 'knowledge_document':
            await self.files.retire_business_source(session, upload.file_id, actor_id=principal.id)
        await self.files.release_resources(
            session, resource_type='knowledge_upload', resource_ids=(upload.id,), actor_id=principal.id
        )
        await self.finish_upload(session, upload.id, 'discarded')
        await session.delete(upload)

    async def document(
        self, session: AsyncSession, base_id: UUID, document_id: UUID, *, include_deleted: bool = False
    ) -> KnowledgeDocument:
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.base_id == base_id, KnowledgeDocument.id == document_id
        )
        if not include_deleted:
            query = query.where(KnowledgeDocument.deleted_at.is_(None))
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise AppError('document_not_found', '文档不存在', 404)
        return row

    async def documents(self, session: AsyncSession, principal: Principal, base_id: UUID) -> DocumentPage:
        base = await self.require(session, principal, base_id)
        engines: dict[UUID | None, str] = dict(
            (
                await session.execute(
                    select(ParsingJob.artifact_id, ParsingJob.engine)
                    .join(KnowledgeJob, KnowledgeJob.id == ParsingJob.parent_id)
                    .where(
                        KnowledgeJob.base_id == base_id,
                        ParsingJob.phase == 'completed',
                        ParsingJob.artifact_id.is_not(None),
                    )
                    .distinct(ParsingJob.artifact_id)
                    .order_by(ParsingJob.artifact_id, ParsingJob.updated_at.desc())
                )
            )
            .tuples()
            .all()
        )
        items = []
        for row in await session.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.base_id == base_id).order_by(KnowledgeDocument.created_at)
        ):
            if row.migration_source_document_id is not None and base.document_policy == 'legacy':
                continue
            if row.deleted_at is not None and (not self.manages(principal, base) or row.status != 'deleting'):
                continue
            try:
                await self.files.require(session, principal, row.file_id, active=True)
            except AppError:
                continue
            if base.document_policy == 'library' or self.manages(principal, base) or row.active_revision:
                items.append(
                    DocumentInfo.model_validate(
                        {
                            **row.__dict__,
                            'error': row.error,
                            'parsing_engine': engines.get(
                                row.artifact_file_id, 'native' if row.artifact_file_id is not None else None
                            ),
                        }
                    )
                )
        return DocumentPage(items=items, total=len(items))

    async def document_info(
        self, session: AsyncSession, base_id: UUID, document_id: UUID, *, include_deleted: bool = False
    ) -> DocumentInfo:
        row = await self.document(session, base_id, document_id, include_deleted=include_deleted)
        return DocumentInfo.model_validate({**row.__dict__, 'error': row.error})

    async def existing_job(
        self, session: AsyncSession, principal: Principal, request_id: UUID, value: str
    ) -> JobInfo | None:
        key = int.from_bytes(hashlib.sha256(principal.id.bytes + request_id.bytes).digest()[:8], 'big', signed=True)
        await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
        row = await session.scalar(
            select(KnowledgeJob).where(
                KnowledgeJob.actor_id == principal.id, KnowledgeJob.client_request_id == request_id
            )
        )
        if row is not None and row.fingerprint != value:
            raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
        return self.job_info(row) if row is not None else None

    async def queue(  # noqa: PLR0912, PLR0915 - lock and freeze native admission facts atomically
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        operation: str,
        request_id: UUID,
        value: str,
        binding: ModelBinding,
        selection: BuildSelection,
    ) -> JobInfo:
        file_ids, document_ids, folder_id = selection.file_ids, selection.document_ids, selection.folder_id
        chunk_size, chunk_overlap = selection.chunk_size, selection.chunk_overlap
        existing = await self.existing_job(session, principal, request_id, value)
        if existing is not None:
            await self.require(session, principal, base_id, manage=True, include_deleted=True)
            return existing
        base = await self.require(
            session, principal, base_id, manage=True, lock=True, include_deleted=operation == 'delete_base'
        )
        self.idle(base)
        await self.validate_import_policy(session, base, file_ids or [])
        chunk_size = base.chunk_size if chunk_size is None else chunk_size
        chunk_overlap = base.chunk_overlap if chunk_overlap is None else chunk_overlap
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        if (
            active
            and active.row_count != 0
            and operation in {'import', 'rechunk'}
            and active.fingerprint != binding_fingerprint(binding)
        ):
            raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请先显式全库重建', 409)
        await self.folder(session, base_id, folder_id)
        owner = await self.identity.principal_for_user(session, base.owner_id)
        docs = []
        existing_docs = []
        for file_id in file_ids or []:
            file = await self.files.require(session, owner, file_id, active=True, lock=True)
            if file.source_file_id is not None:
                raise AppError('invalid_source', '请使用原始受管文件', 422)
            uploaded = await session.scalar(select(KnowledgeUpload).where(KnowledgeUpload.file_id == file_id))
            if uploaded is not None and (uploaded.base_id != base_id or uploaded.folder_id != folder_id):
                raise AppError('invalid_source', '上传不属于此知识库或文件夹', 422)
            material = await session.get(KnowledgeMaterial, file.id) if base.document_policy == 'library' else None
            if material is not None and material.document_id is not None:
                existing_docs.append(await self.document(session, base_id, material.document_id))
                continue
            row = KnowledgeDocument(
                base_id=base_id,
                file_id=file.id,
                origin_file_id=uploaded.origin_file_id if uploaded else None,
                folder_id=folder_id,
                file_name=file.original_filename,
                file_size=file.size,
                mime=file.mime,
                source_sha256=file.sha256,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            session.add(row)
            await session.flush()
            if base.document_policy == 'library':
                material = await session.get(KnowledgeMaterial, file.id)
                if material is None:
                    raise AppError('invalid_library_source', '知识库资料需要独立副本', 422)
                material.document_id = row.id
            snapshot = await self.files.source_snapshot(session, owner, file.id)
            await self.files.bind_sources(
                session, owner, (snapshot,), resource_type='knowledge_document', resource_id=row.id
            )
            if uploaded:
                await self.files.release_resources(
                    session, resource_type='knowledge_upload', resource_ids=(uploaded.id,), actor_id=principal.id
                )
                await self.finish_upload(session, uploaded.id, 'consumed')
                await session.delete(uploaded)
            docs.append(row)
        for document_id in document_ids or []:
            row = await self.document(session, base_id, document_id, include_deleted=operation.startswith('delete'))
            docs.append(row)
        if not docs and existing_docs:
            # Persist the new request identity without creating documents or model work.
            job = KnowledgeJob(
                base_id=base_id,
                actor_id=principal.id,
                client_request_id=request_id,
                fingerprint=value,
                operation=operation,
                status='completed',
                phase='completed',
                index_revision=base.active_revision or uuid4(),
                finished_at=utcnow(),
                documents=[
                    DocumentResult(
                        document_id=row.id, file_id=row.file_id, status='ready', chunk_count=row.chunk_count
                    ).model_dump(mode='json')
                    for row in existing_docs
                ],
            )
            session.add(job)
            await session.flush()
            return self.job_info(job)
        job = KnowledgeJob(
            base_id=base_id,
            actor_id=principal.id,
            client_request_id=request_id,
            fingerprint=value,
            operation=operation,
            attempt_id=uuid4(),
            index_revision=uuid4(),
        )
        session.add(job)
        await session.flush()
        for row in docs:
            row.job_id, row.status = job.id, 'deleting' if operation.startswith('delete') else 'processing'
            if operation in {'import', 'rechunk', 'rebuild'}:
                row.chunk_size, row.chunk_overlap = chunk_size, chunk_overlap
            row.version += 1
            row.updated_at = utcnow()
        job.documents = [
            DocumentResult(document_id=row.id, file_id=row.file_id, status='pending').model_dump(mode='json')
            for row in docs
        ]
        if operation.startswith('delete'):
            await self._tombstone(session, principal, base, job, docs)
        generation = IndexGeneration(
            id=job.index_revision,
            base_id=base_id,
            attempt_id=job.attempt_id,
            collection_name=f'zh_k_{base_id.hex}_{job.index_revision.hex}_{job.attempt_id.hex}',
            binding=binding.model_dump(mode='json'),
            fingerprint=binding_fingerprint(binding),
            retrieval_version=2,
            dimension=active.dimension
            if active is not None and operation != 'rebuild' and active.fingerprint == binding_fingerprint(binding)
            else None,
        )
        session.add(generation)
        base.active_job_id, base.updated_at = job.id, utcnow()
        await session.flush()
        return self.job_info(job)

    async def _tombstone(
        self,
        session: AsyncSession,
        principal: Principal,
        base: KnowledgeBase,
        job: KnowledgeJob,
        docs: list[KnowledgeDocument],
    ) -> None:
        base_id = base.id
        job.phase = 'write'
        job.documents = [{**entry, 'status': 'ready', 'chunk_count': 0} for entry in job.documents]
        for row in docs:
            row.deleted_at = utcnow()
            await self.retire_material(session, row.file_id, actor_id=principal.id)
        if job.operation == 'delete_base':
            base.deleted_at = utcnow()
            await retire_base_graph(session, base.id)
            for folder in await session.scalars(select(KnowledgeFolder).where(KnowledgeFolder.base_id == base_id)):
                folder.deleted_at = utcnow()
            await self.release_uploads(session, principal, base_id)

    @staticmethod
    def job_info(job: KnowledgeJob) -> JobInfo:
        results = [DocumentResult.model_validate(row) for row in job.documents]
        return JobInfo(
            id=job.id,
            base_id=job.base_id,
            operation=job.operation,
            status=job.status,
            phase=job.phase,
            version=job.version,
            attempt_id=job.attempt_id,
            index_revision=job.index_revision,
            total_documents=len(results),
            completed_documents=sum(row.status == 'ready' for row in results),
            failed_documents=sum(row.status == 'failed' for row in results),
            chunk_count=sum(row.chunk_count or 0 for row in results)
            if results and all(row.chunk_count is not None for row in results)
            else None,
            document_results=results,
            error=RunError.model_validate(job.error) if job.error else None,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
            cancel_allowed=job.status not in JOB_TERMINAL,
        )

    async def get_job(self, session: AsyncSession, principal: Principal, job_id: UUID) -> KnowledgeJob:
        job = await session.get(KnowledgeJob, job_id)
        if job is None:
            raise AppError('knowledge_job_not_found', '知识任务不存在', 404)
        await self.require(session, principal, job.base_id, manage=True, include_deleted=True)
        return job

    async def get_job_info(self, session: AsyncSession, principal: Principal, job_id: UUID) -> JobInfo:
        return self.job_info(await self.get_job(session, principal, job_id))

    async def cancel_job(self, session: AsyncSession, principal: Principal, job_id: UUID) -> JobInfo:
        job = await session.scalar(
            select(KnowledgeJob)
            .where(KnowledgeJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise AppError('knowledge_job_not_found', '知识任务不存在', 404)
        await self.require(session, principal, job.base_id, manage=True, include_deleted=True)
        if job.status not in JOB_TERMINAL and job.status != 'cancel_requested':
            job.status, job.version, job.updated_at = 'cancel_requested', job.version + 1, utcnow()
        return self.job_info(job)

    async def all_document_ids(
        self, session: AsyncSession, base_id: UUID, *, include_tombstoned: bool = False
    ) -> list[UUID]:
        query = select(KnowledgeDocument.id).where(KnowledgeDocument.base_id == base_id)
        base = await session.get(KnowledgeBase, base_id)
        if base is not None and base.document_policy == 'legacy' and not include_tombstoned:
            query = query.where(KnowledgeDocument.migration_source_document_id.is_(None))
        query = query.where(
            KnowledgeDocument.status != 'deleted' if include_tombstoned else KnowledgeDocument.deleted_at.is_(None)
        )
        return list(await session.scalars(query))

    async def release_uploads(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_ids: set[UUID] | None = None
    ) -> None:
        query = select(KnowledgeUpload).where(KnowledgeUpload.base_id == base_id)
        if folder_ids is not None:
            query = query.where(KnowledgeUpload.folder_id.in_(folder_ids))
        for upload in await session.scalars(query):
            await self.retire_material(session, upload.file_id, actor_id=principal.id)
            await self.files.release_resources(
                session, resource_type='knowledge_upload', resource_ids=(upload.id,), actor_id=principal.id
            )
            await session.delete(upload)

    async def deletion_scope(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID
    ) -> list[UUID]:
        await self.folder(session, base_id, folder_id, include_deleted=True)
        folders = list(await session.scalars(select(KnowledgeFolder).where(KnowledgeFolder.base_id == base_id)))
        ids = {folder_id}
        while True:
            expanded = ids | {folder.id for folder in folders if folder.parent_id in ids}
            if expanded == ids:
                break
            ids = expanded
        for folder in folders:
            if folder.id in ids:
                folder.deleted_at = utcnow()
        await self.release_uploads(session, principal, base_id, ids)
        return list(
            await session.scalars(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.base_id == base_id,
                    KnowledgeDocument.folder_id.in_(ids),
                    KnowledgeDocument.status != 'deleted',
                )
            )
        )

    async def jobs(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[JobInfo]:
        await self.require(session, principal, base_id, manage=True, include_deleted=True)
        return [
            self.job_info(row)
            for row in await session.scalars(
                select(KnowledgeJob)
                .where(KnowledgeJob.base_id == base_id)
                .order_by(KnowledgeJob.created_at.desc())
                .limit(50)
            )
        ]

    async def pending_uploads(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID | None = None
    ) -> dict[str, object]:
        base = await self.require(session, principal, base_id, manage=True)
        self.idle(base)
        await self.folder(session, base_id, folder_id)
        query = select(KnowledgeUpload).where(
            KnowledgeUpload.base_id == base_id,
            KnowledgeUpload.folder_id == folder_id,
            KnowledgeUpload.created_at > utcnow() - timedelta(hours=24),
        )
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = await session.scalars(query.order_by(KnowledgeUpload.created_at, KnowledgeUpload.id).limit(20))
        items = []
        for row in rows:
            file = await self.files.get(session, principal, row.file_id)
            receipt = await session.scalar(
                select(KnowledgeUploadReceipt).where(KnowledgeUploadReceipt.upload_id == row.id)
            )
            items.append(
                {
                    'request_id': receipt.request_id if receipt else row.id,
                    'upload_id': row.id,
                    'file': file.model_dump(mode='json'),
                    'folder_id': row.folder_id,
                    'created_at': row.created_at,
                }
            )
        return {'items': items, 'total': total}
