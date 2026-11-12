"""Knowledge document workflows own external IO, transaction boundaries and compensation."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from zhiheng.application.files.inputs import FileInputService
from zhiheng.application.files.objects import FileObjects
from zhiheng.application.knowledge.native import NativeKnowledgeCommands
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileInfo, FileService
from zhiheng.modules.knowledge.public import (
    NATIVE_FORMATS,
    AttachmentImport,
    AttachmentImportInfo,
    BuildSelection,
    DocumentImport,
    JobInfo,
    KnowledgeService,
    MigrationRequest,
    fingerprint,
    split_chunks,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class KnowledgeDocuments:
    def __init__(
        self,
        database: Database,
        knowledge: KnowledgeService,
        files: FileService,
        inputs: FileInputService,
        objects: FileObjects,
    ) -> None:
        self.database, self.knowledge, self.files = database, knowledge, files
        self.inputs, self.objects = inputs, objects

    @asynccontextmanager
    async def serialize_import(self, namespace_id: UUID, request_id: UUID) -> AsyncIterator[None]:
        key = int.from_bytes(
            hashlib.sha256(b'knowledge-copy' + namespace_id.bytes + request_id.bytes).digest()[:8], 'big', signed=True
        )
        # A request lock spans external IO; source/base rows are locked only inside their own short transactions.
        async with self.database.sessions.begin() as guard:
            await guard.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
            yield

    async def stage_upload(
        self,
        principal: Principal,
        base_id: UUID,
        *,
        filename: str,
        content: bytes,
        folder_id: UUID | None = None,
        client_request_id: UUID | None = None,
    ) -> dict[str, object]:
        request_id = client_request_id or uuid4()
        async with self.serialize_import(principal.id, request_id):
            return await self._stage_upload(
                principal, base_id, filename=filename, content=content, folder_id=folder_id, request_id=request_id
            )

    async def _stage_upload(
        self,
        principal: Principal,
        base_id: UUID,
        *,
        filename: str,
        content: bytes,
        folder_id: UUID | None,
        request_id: UUID,
    ) -> dict[str, object]:
        value = fingerprint([str(base_id), filename, str(folder_id), hashlib.sha256(content).hexdigest()])
        async with self.database.sessions.begin() as session:
            previous = await self.knowledge.upload_receipt(session, principal, base_id, request_id, value)
            if previous is not None:
                return previous
            base = await self.knowledge.authorize_base(session, principal, base_id, manage=True)
            self.knowledge.idle(base)
            info = await self.knowledge.detail(session, principal, base_id)
            if info.index_status == 'reindex_required':
                raise AppError('knowledge_reindex_required', '请先在知识库设置中修复索引，再添加资料', 409)
            owner_id = base.owner_id
            await self.knowledge.authorize_folder(session, base_id, folder_id)
        if filename.rpartition('.')[2].lower() not in NATIVE_FORMATS:
            raise AppError('unsupported_knowledge_format', '该格式不能加入知识库', 422)
        if not content or len(content) > 20 * 1024 * 1024:
            raise AppError('invalid_file_size', '文件为空或超过20MiB', 422)
        key = await self.files.storage.put(content)
        async with self.objects.registration(key) as session:
            file = await self.files.create_owned(
                session,
                principal,
                owner_id=owner_id,
                filename=filename,
                content=content,
                storage_key=key,
                purpose='knowledge_document',
            )
            upload_id = await self.knowledge.register_upload(session, principal, base_id, file, folder_id)
            await self.knowledge.remember_upload(
                session, principal, base_id, request_id, value, file.id, upload_id, folder_id
            )
            file = await self.files.get(session, principal, file.id)
        return {'file': file.model_dump(mode='json'), 'upload_id': upload_id, 'folder_id': folder_id}

    async def upload_status(self, principal: Principal, base_id: UUID, request_id: UUID) -> dict[str, object]:
        async with self.serialize_import(principal.id, request_id), self.database.sessions() as session:
            return await self.knowledge.find_upload_receipt(session, principal, base_id, request_id)

    async def cleanup_uploads(self) -> None:
        async with self.database.sessions() as session:
            expired = await self.knowledge.expired_uploads(session, utcnow() - timedelta(hours=24))
        for base_id, owner_id, upload_id in expired:
            async with self.database.sessions.begin() as session:
                try:
                    await self.knowledge.remove_upload(
                        session, Principal(owner_id, 'knowledge-upload-maintenance', False), base_id, upload_id
                    )
                    await self.knowledge.finish_upload(session, upload_id, 'expired')
                except AppError as error:
                    if error.code not in {'knowledge_busy', 'knowledge_upload_not_found', 'knowledge_not_found'}:
                        raise

    async def prepare_import(self, principal: Principal, base_id: UUID, selection: BuildSelection) -> BuildSelection:
        async with self.database.sessions.begin() as session:
            sources = await self.knowledge.import_sources(
                session, principal, base_id, selection.file_ids or [], selection.folder_id
            )
        mapping = {}
        for source in sources:
            async with self.database.sessions.begin() as session:
                existing = await self.knowledge.existing_attachment(session, principal, base_id, source.id)
            if existing is not None:
                mapping[source.id] = existing.document.file_id
                continue
            async with self.database.sessions() as session:
                ticket = await self.files.read_ticket(session, principal, source.id)
            content = await self.files.read_content(ticket)
            key = await self.files.storage.put(content)
            async with self.objects.registration(key) as session:
                await self.knowledge.lock_library_sources(
                    session, principal, (source.id,), additional_base_ids=(base_id,)
                )
                current = await self.files.require(session, principal, source.id, active=True, lock=True)
                if current.owner_id != principal.id or current.sha256 != source.sha256:
                    raise AppError('source_snapshot_changed', '原附件已变化，请重试', 409)
                copied = await self.knowledge.copied_source_file(
                    session, principal, base_id, source.id, selection.folder_id
                )
                if copied is not None:
                    mapping[source.id] = copied
                    continue
                file = await self.files.create_owned(
                    session,
                    principal,
                    owner_id=principal.id,
                    filename=source.original_filename,
                    content=content,
                    storage_key=key,
                    purpose='knowledge_document',
                )
                await self.knowledge.register_copy_upload(
                    session, principal, base_id, current, file, selection.folder_id
                )
                mapping[source.id] = file.id
        return replace(selection, file_ids=[mapping.get(value, value) for value in selection.file_ids or []])

    async def import_files(self, principal: Principal, base_id: UUID, body: DocumentImport) -> JobInfo:
        async with self.serialize_import(principal.id, body.client_request_id):
            return await self._import_files(principal, base_id, body)

    async def _import_files(self, principal: Principal, base_id: UUID, body: DocumentImport) -> JobInfo:
        native = NativeKnowledgeCommands(self.knowledge, self.knowledge.models)
        payload = body.model_dump(mode='json')
        value = fingerprint(['import', str(base_id), payload])
        async with self.database.sessions.begin() as session:
            await self.knowledge.authorize_base(session, principal, base_id, manage=True)
            existing = await self.knowledge.existing_job(session, principal, body.client_request_id, value)
            if existing is not None:
                return existing
        selection = await self.prepare_import(
            principal,
            base_id,
            BuildSelection(
                file_ids=body.file_ids,
                folder_id=body.folder_id,
                chunk_size=body.chunk_size if 'chunk_size' in body.model_fields_set else None,
                chunk_overlap=body.chunk_overlap if 'chunk_overlap' in body.model_fields_set else None,
            ),
        )
        async with self.database.sessions.begin() as session:
            imported = await self.knowledge.imported_files_job(session, principal, base_id, selection.file_ids or [])
            if imported is not None:
                return imported
            return await native.console_job(
                session, principal, base_id, 'import', body.client_request_id, payload, selection
            )

    async def add_attachment(self, principal: Principal, base_id: UUID, body: AttachmentImport) -> AttachmentImportInfo:
        async with self.database.sessions.begin() as session:
            await self.files.require_sent_attachment(session, principal, body.file_id)
            base = await self.knowledge.require(session, principal, base_id, manage=True)
            if base.document_policy != 'library':
                raise AppError('knowledge_migration_required', '请先迁移此知识库后再加入聊天附件', 409)
            existing = await self.knowledge.existing_attachment(session, principal, base_id, body.file_id)
            if existing is not None:
                return existing
            size, overlap = base.chunk_size, base.chunk_overlap
        job = await self.import_files(
            principal,
            base_id,
            DocumentImport(
                file_ids=[body.file_id],
                folder_id=body.folder_id,
                client_request_id=body.client_request_id,
                chunk_size=size,
                chunk_overlap=overlap,
            ),
        )
        async with self.database.sessions.begin() as session:
            # Recheck the source and target after storage IO, without disclosing the origin to readers.
            await self.files.require_sent_attachment(session, principal, body.file_id)
            result = await self.knowledge.existing_attachment(session, principal, base_id, body.file_id)
            assert result is not None
            return result.model_copy(update={'job': job, 'already_exists': False})

    async def download(self, principal: Principal, base_id: UUID, document_id: UUID) -> tuple[FileInfo, bytes]:
        async with self.database.sessions.begin() as session:
            file = await self.knowledge.require_document_source(session, principal, base_id, document_id)
            ticket = await self.files.read_ticket(session, principal, file.id)
        content = await self.files.read_content(ticket)
        async with self.database.sessions.begin() as session:
            await self.knowledge.require_document_source(session, principal, base_id, document_id)
        return file, content

    async def migrate(self, principal: Principal, base_id: UUID, body: MigrationRequest) -> JobInfo:
        async with self.serialize_import(principal.id, body.client_request_id):
            return await self._migrate(principal, base_id, body)

    async def _migrate(self, principal: Principal, base_id: UUID, body: MigrationRequest) -> JobInfo:
        value = fingerprint(['migration', str(base_id), body.model_dump(mode='json')])
        async with self.database.sessions.begin() as session:
            await self.knowledge.authorize_base(session, principal, base_id, manage=True)
            existing = await self.knowledge.existing_job(session, principal, body.client_request_id, value)
            if existing is not None:
                return existing
            sources = await self.knowledge.migration_sources(session, principal, base_id, body)
        document_ids: list[UUID] = []
        identities: list[dict[str, object]] = []
        for source in sources:
            async with self.database.sessions.begin() as session:
                snapshot = await self.files.source_snapshot(session, principal, source.file_id)
                existing_copy = await self.knowledge.existing_migration_copy(session, base_id, source, snapshot.sha256)
                ticket = await self.files.read_ticket(session, principal, source.file_id)
            if existing_copy is None:
                content = await self.files.read_content(ticket)
                key = await self.files.storage.put(content)
                async with self.objects.registration(key) as session:
                    await self.knowledge.lock_library_sources(
                        session, principal, (source.file_id,), additional_base_ids=(base_id,)
                    )
                    current = await self.files.source_snapshot(session, principal, source.file_id, lock=True)
                    if current != snapshot:
                        raise AppError('source_snapshot_changed', '原资料已变化，请重新预览迁移', 409)
                    file = await self.files.create_owned(
                        session,
                        principal,
                        owner_id=principal.id,
                        filename=source.file_name,
                        content=content,
                        storage_key=key,
                        purpose='knowledge_document',
                    )
                    existing_copy = await self.knowledge.migration_copy(session, principal, base_id, source, file)
            document_ids.append(existing_copy)
            identities.append(
                {
                    'source_document_id': str(source.id),
                    'document_id': str(existing_copy),
                    'source_file_id': str(source.file_id),
                    'source_snapshot': snapshot.model_dump(mode='json'),
                }
            )
        async with self.database.sessions.begin() as session:
            return await self.knowledge.queue_migration(session, principal, base_id, body, document_ids, identities)

    async def preview(self, principal: Principal, base_id: UUID, document_id: UUID) -> dict[str, object]:
        async with self.database.sessions.begin() as session:
            await self.knowledge.authorize_base(session, principal, base_id)
            document = await self.knowledge.document_info(session, base_id, document_id)
            await self.knowledge.require_document_source(session, principal, base_id, document_id)
            file_id, size, overlap = document.file_id, document.chunk_size, document.chunk_overlap
            artifact_id = (await self.knowledge.document(session, base_id, document_id)).artifact_file_id
        parsed = (
            await self.inputs.parse_artifact(principal, file_id, artifact_id)
            if artifact_id is not None
            else await self.inputs.parse(principal, file_id)
        )
        if str(parsed.metadata.get('format', '')).lower() not in (*NATIVE_FORMATS, 'markdown'):
            raise AppError('ocr_required', '图像知识预览需要OCR解析', 422)
        try:
            count = len(split_chunks(parsed.text, size, overlap))
        except ValueError:
            raise AppError('invalid_chunks', '知识分块无效或超过2000块', 422) from None
        async with self.database.sessions.begin() as session:
            await self.knowledge.authorize_base(session, principal, base_id)
            await self.knowledge.document_info(session, base_id, document_id)
            await self.knowledge.require_document_source(session, principal, base_id, document_id)
        return {
            'document_id': document_id,
            'file_id': file_id,
            'text': parsed.text,
            'parser_version': parsed.parser_version,
            'chunk_count': count,
        }
