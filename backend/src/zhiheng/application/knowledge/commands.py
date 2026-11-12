"""Resource admission into the original knowledge domain and unified model accounting."""

from dataclasses import asdict
from typing import TYPE_CHECKING, cast
from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.application.knowledge.native import NativeKnowledgeCommands
from zhiheng.application.knowledge.preparation import KnowledgePreparation
from zhiheng.application.operations.contracts import OperationInfo
from zhiheng.application.operations.queries import OperationQueries
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.knowledge.public import (
    BaseCreate,
    BaseInfo,
    BasePatch,
    BuildSelection,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    KnowledgeService,
    QueryRequest,
    fingerprint,
)
from zhiheng.modules.models.public import ModelService
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from zhiheng.application.knowledge.documents import KnowledgeDocuments
    from zhiheng.modules.developer.public import ResourceAction, ResourceKind


def target(kind: str, identifier: UUID, action: str) -> dict[str, str]:
    return {'kind': kind, 'id': str(identifier), 'action': action}


class KnowledgeCommands:
    def __init__(
        self,
        database: Database,
        knowledge: KnowledgeService,
        models: ModelService,
        usage: UsageService,
        access: ResourceAccessService,
        preparation: KnowledgePreparation,
        operations: OperationQueries,
    ) -> None:
        self.database, self.knowledge, self.models, self.usage, self.access, self.preparation, self.operations = (
            database,
            knowledge,
            models,
            usage,
            access,
            preparation,
            operations,
        )
        self.native = NativeKnowledgeCommands(knowledge, models)
        self.documents: KnowledgeDocuments | None = None

    async def create(self, caller: CallerContext, body: BaseCreate, key: UUID) -> BaseInfo:
        async with self.database.sessions.begin() as session:
            request = await self.access.begin_request(
                session, caller, 'knowledge.create', key, body.model_dump(mode='json'), scope='knowledge:write'
            )
            if request.replayed:
                return BaseInfo.model_validate(request.snapshot)
            prepared = await self.knowledge.creation_parameters(session, body)
            assert prepared.embed_model_id is not None and caller.identity is not None
            await self.access.keys.authorize_identity(
                session, caller.identity, [prepared.embed_model_id], scope='knowledge:write', lock=True
            )
            info = await self.native.create(session, self.access.personal(caller.principal), prepared)
            await self.access.autobind(session, caller, 'knowledge_base', info.id)
            await self.access.requests.complete(
                session,
                request.id,
                result_kind='knowledge_base',
                result_id=info.id,
                snapshot=info.model_dump(mode='json'),
                resources=(target('knowledge_base', info.id, 'write'),),
            )
            return info

    async def patch(self, caller: CallerContext, base_id: UUID, body: BasePatch) -> BaseInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'write')
            base = await self.knowledge.authorize_base(session, caller.principal, base_id, manage=True, lock=True)
            self.precondition(base.version, body.expected_version)
            return await self.native.patch(session, caller.principal, base_id, body)

    async def folder_create(self, caller: CallerContext, base_id: UUID, body: FolderCreate, key: UUID) -> FolderInfo:
        async with self.database.sessions.begin() as session:
            request = await self.access.begin_request(
                session,
                caller,
                'knowledge.folder.create',
                key,
                {'base_id': str(base_id), **body.model_dump(mode='json')},
                scope='knowledge:write',
                resources=(target('knowledge_base', base_id, 'write'),),
            )
            if request.replayed:
                return FolderInfo.model_validate(request.snapshot)
            result = await self.native.folder_create(session, caller.principal, base_id, body)
            await self.access.requests.complete(
                session,
                request.id,
                result_kind='knowledge_folder',
                result_id=result.id,
                snapshot=result.model_dump(mode='json'),
            )
            return result

    async def folder_patch(
        self, caller: CallerContext, base_id: UUID, folder_id: UUID, body: FolderPatch
    ) -> FolderInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'write')
            folder = await self.knowledge.authorize_folder(session, base_id, folder_id)
            assert folder is not None
            self.precondition(folder.version, body.expected_version)
            return await self.native.folder_patch(session, caller.principal, base_id, folder_id, body)

    @staticmethod
    def precondition(actual: int, expected: int) -> None:
        if actual != expected:
            raise AppError('precondition_failed', '资源版本已变化', 412)

    async def job(
        self,
        caller: CallerContext,
        base_id: UUID,
        operation: str,
        key: UUID,
        *,
        selection: BuildSelection | None = None,
        expected_version: int | None = None,
        child_id: UUID | None = None,
    ) -> OperationInfo:
        if operation == 'import' and self.documents is not None:
            namespace = caller.identity.application_id if caller.identity else caller.principal.id
            async with self.documents.serialize_import(namespace, key):
                return await self._job(
                    caller,
                    base_id,
                    operation,
                    key,
                    selection=selection,
                    expected_version=expected_version,
                    child_id=child_id,
                )
        return await self._job(
            caller, base_id, operation, key, selection=selection, expected_version=expected_version, child_id=child_id
        )

    async def _job(  # noqa: PLR0912, PLR0915 - admission rechecks identity and domain state around file IO
        self,
        caller: CallerContext,
        base_id: UUID,
        operation: str,
        key: UUID,
        *,
        selection: BuildSelection | None = None,
        expected_version: int | None = None,
        child_id: UUID | None = None,
    ) -> OperationInfo:
        selection = selection or BuildSelection()
        payload = {
            'base_id': str(base_id),
            'child_id': str(child_id) if child_id else None,
            'expected_version': expected_version,
            'selection': {
                k: ([str(i) for i in v] if isinstance(v, list) else str(v) if isinstance(v, UUID) else v)
                for k, v in asdict(selection).items()
            },
        }
        # Only explicit input identity belongs to the hash; derived document/source sets may evolve after completion.
        inputs = (
            target('knowledge_base', base_id, 'delete' if operation == 'delete_base' else 'write'),
            *(target('file', file_id, 'read') for file_id in sorted(selection.file_ids or [], key=str)),
        )
        prepared_import = None
        request_inputs = inputs
        library_policy = False
        if operation == 'import' and self.documents is not None:
            # Commit only the resumable request identity before external file IO; no domain locks cross it.
            async with self.database.sessions.begin() as session:
                caller = await self.access.current(session, caller, 'knowledge:write')
                assert caller.identity is not None
                current_base = await self.knowledge.require(session, caller.principal, base_id, manage=True)
                library_policy = current_base.document_policy == 'library'
                request_inputs = inputs[:1] if library_policy else inputs
                pending = await self.access.requests.begin(
                    session,
                    caller.identity,
                    'knowledge.' + operation,
                    key,
                    cast('dict[str, object]', payload),
                    scope='knowledge:write',
                    resources=request_inputs,
                )
                if pending.replayed and pending.result_id is not None:
                    await self.access.authorize_request(session, caller, pending)
                    return await self.operations.project(session, caller, pending)
                roots = await self.knowledge.files.source_root_ids(session, tuple(selection.file_ids or []))
                await self.knowledge.lock_library_sources(
                    session, caller.principal, roots, additional_base_ids=(base_id,)
                )
                for item in inputs:
                    await self.access.require(
                        session,
                        caller,
                        cast('ResourceKind', item['kind']),
                        UUID(item['id']),
                        cast('ResourceAction', item['action']),
                    )
            prepared_import = await self.documents.prepare_import(caller.principal, base_id, selection)
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'knowledge:write')
            assert caller.identity is not None
            current_base = await self.knowledge.require(
                session, caller.principal, base_id, manage=True, include_deleted=operation == 'delete_base'
            )
            library_policy = current_base.document_policy == 'library'
            request_inputs = inputs[:1] if library_policy else inputs
            request = await self.access.requests.begin(
                session,
                caller.identity,
                'knowledge.' + operation,
                key,
                cast('dict[str, object]', payload),
                scope='knowledge:write',
                resources=request_inputs,
            )
            if request.replayed and request.result_id is not None:
                await self.access.authorize_request(session, caller, request)
                return await self.operations.project(session, caller, request)
            await lock_billing_admission(session)
            await self.access.require(
                session,
                caller,
                'knowledge_base',
                base_id,
                'delete' if operation == 'delete_base' else 'write',
                lock=False,
            )
            base = await self.knowledge.authorize_base(session, caller.principal, base_id, manage=True)
            if operation.startswith('delete'):
                binding = await self.knowledge.deletion_binding(session, base_id)
            else:
                await self.access.keys.authorize_identity(
                    session, caller.identity, [base.embed_model_id], scope='knowledge:write', lock=True
                )
                binding = (await self.models.resolve_many(session, [base.embed_model_id]))[base.embed_model_id]
                await self.preparation.admission_rate(session, base.embed_model_id, caller.identity)
            if operation == 'import':
                roots = await self.knowledge.files.source_root_ids(session, tuple(selection.file_ids or []))
                await self.knowledge.lock_library_sources(
                    session, caller.principal, roots, additional_base_ids=(base_id,)
                )
            await self.access.require(
                session, caller, 'knowledge_base', base_id, 'delete' if operation == 'delete_base' else 'write'
            )
            base = await self.knowledge.authorize_base(session, caller.principal, base_id, manage=True, lock=True)
            self.knowledge.idle(base)
            if operation in {'rebuild', 'delete_base'}:
                assert expected_version is not None
                self.precondition(base.version, expected_version)
            elif operation in {'rechunk', 'delete_document'}:
                assert child_id is not None and expected_version is not None
                document = await self.knowledge.document_info(session, base_id, child_id)
                self.precondition(document.version, expected_version)
            elif operation == 'delete_folder':
                assert child_id is not None and expected_version is not None
                folder = await self.knowledge.authorize_folder(session, base_id, child_id)
                assert folder is not None
                self.precondition(folder.version, expected_version)
            selection = await self.native.select(
                session, caller.principal, base_id, operation, selection, expected_version, child_id
            )
            sources = set(selection.file_ids or [])
            if not operation.startswith('delete'):
                for document_id in selection.document_ids or []:
                    sources.add(
                        (
                            await self.knowledge.document_info(session, base_id, document_id, include_deleted=True)
                        ).file_id
                    )
            resources = (
                request_inputs
                if library_policy
                else (*inputs, *(target('file', file_id, 'read') for file_id in sorted(sources, key=str)))
            )
            for file_id in sorted(sources, key=str):
                if library_policy and operation != 'import':
                    await self.knowledge.files.require(session, caller.principal, file_id, active=True)
                else:
                    await self.access.require(session, caller, 'file', file_id, 'read')
            if prepared_import is not None:
                selection = prepared_import
            job = await self.native.queue(
                session,
                caller.principal,
                base_id,
                operation,
                request.native_request_id,
                fingerprint([operation, payload]),
                binding,
                selection,
            )
            request = await self.access.requests.complete(
                session,
                request.id,
                result_kind='knowledge_job',
                result_id=job.id,
                snapshot={'native_id': str(job.id)},
                resources=resources,
            )
            return await self.operations.project(session, caller, request)

    async def query(self, caller: CallerContext, base_id: UUID, body: QueryRequest, key: UUID) -> OperationInfo:
        payload = body.model_dump(mode='json', exclude={'client_request_id'})
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'knowledge:query')
            assert caller.identity is not None
            request = await self.access.requests.begin(
                session,
                caller.identity,
                'knowledge.query',
                key,
                payload,
                scope='knowledge:query',
                resources=(target('knowledge_base', base_id, 'query'),),
            )
            if request.replayed:
                await self.access.authorize_request(session, caller, request)
                return await self.operations.project(session, caller, request)
            await self.access.require(session, caller, 'knowledge_base', base_id, 'query', lock=False)
            base = await self.knowledge.authorize_base(session, self.access.personal(caller.principal), base_id)
            await self.access.keys.authorize_identity(
                session, caller.identity, [base.embed_model_id], scope='knowledge:query', lock=True
            )
            await self.models.resolve_many(session, [base.embed_model_id])
            await self.preparation.admission_rate(
                session, base.embed_model_id, caller.identity, len(body.query.encode()) + 16
            )
            await self.access.require(session, caller, 'knowledge_base', base_id, 'query')
            native = body.model_copy(update={'client_request_id': request.native_request_id})
            info = await self.preparation.query(
                session,
                self.access.personal(caller.principal),
                base_id,
                native,
                identity=caller.identity,
                namespace_id=request.application_id,
            )
            request = await self.access.requests.complete(
                session,
                request.id,
                result_kind='knowledge_preparation',
                result_id=info.id,
                snapshot={'native_id': str(info.id)},
            )
            return await self.operations.project(session, caller, request)
