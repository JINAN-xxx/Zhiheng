"""Knowledge catalog, base authorization and explicit domain composition."""

from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.public import AclEntry, BuiltinPermission, FileService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.build_admission import KnowledgeBuildAdmission
from zhiheng.modules.knowledge.contracts import (
    BaseAccess,
    BaseAcl,
    BaseAclInfo,
    BaseCreate,
    BaseInfo,
    BasePatch,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    RerankPolicy,
    UnavailableBaseInfo,
)
from zhiheng.modules.knowledge.graph.orm import GraphConfiguration, GraphGeneration, GraphJob
from zhiheng.modules.knowledge.materials import KnowledgeMaterials
from zhiheng.modules.knowledge.migration import KnowledgeMigration
from zhiheng.modules.knowledge.orm import (
    IndexGeneration,
    KnowledgeBase,
    KnowledgeDefaults,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeGrant,
)
from zhiheng.modules.knowledge.references import KnowledgeReferences, PreparationSkillOwner
from zhiheng.modules.knowledge.values import binding_fingerprint
from zhiheng.modules.models.public import ModelBinding, ModelService
from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


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
        self.materials = KnowledgeMaterials(self)
        self.builds = KnowledgeBuildAdmission(self, self.materials)
        self.migration = KnowledgeMigration(self, self.materials, self.builds)
        self.references = KnowledgeReferences(
            self, preparation_skill_owner=preparation_skill_owner, base_references=base_references
        )

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
            binding = await self.models.bindings.resolve(session, model_id, lock=False)
        except AppError as error:
            raise AppError('knowledge_rerank_unavailable', '重排模型不可用，请检查启用和连接状态', 409) from error
        if binding.model_type != 'rerank':
            raise AppError('invalid_knowledge_model', '请选择已启用的重排模型', 422)
        return binding

    async def default_rerank_binding(self, session: AsyncSession) -> ModelBinding:
        return await self._rerank_binding(session, await self.required_rerank_id(session))

    async def retrieval_rerank_binding(self, session: AsyncSession, base_ids: list[UUID]) -> ModelBinding:
        policy = await self._retrieval_rerank_choice(session, base_ids)
        return await self._rerank_binding(session, policy.model_id)

    async def retrieval_rerank_policy(self, session: AsyncSession, base_ids: list[UUID]) -> RerankPolicy:
        policy = await self._retrieval_rerank_choice(session, base_ids)
        await self._rerank_binding(session, policy.model_id)
        return policy

    async def _retrieval_rerank_choice(self, session: AsyncSession, base_ids: list[UUID]) -> RerankPolicy:
        bases = list(await session.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(base_ids))))
        choices = {(base.use_default_rerank, base.rerank_model_id) for base in bases}
        if len(choices) != 1 or next(iter(choices))[0]:
            return RerankPolicy(
                model_id=await self.required_rerank_id(session),
                source='mixed_platform_default' if len(choices) > 1 else 'platform_default',
            )
        model_id = await self.required_rerank_id(session, use_default=False, model_id=next(iter(choices))[1])
        return RerankPolicy(model_id=model_id, source='knowledge_base')

    async def defaults_ready(self, session: AsyncSession) -> bool:
        defaults = await session.get(KnowledgeDefaults, 1)
        if defaults is None or defaults.default_embed_model_id is None:
            return False
        try:
            binding = await self.models.bindings.resolve(session, defaults.default_embed_model_id, lock=False)
            return binding.model_type == 'embedding'
        except AppError:
            return False

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
                binding = await self.models.bindings.resolve(session, base.embed_model_id, lock=False)
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
            graph_binding = await self.models.bindings.resolve(session, body.llm_model_id)
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
