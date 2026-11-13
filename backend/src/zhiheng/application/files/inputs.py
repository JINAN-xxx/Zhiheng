"""Prepare untrusted attachment bytes outside locked admission transactions."""

from uuid import UUID

from pydantic import BaseModel

from zhiheng.application.files.objects import FileObjects
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import AttachmentSummary, FileService, PreparedAttachment
from zhiheng.modules.knowledge.public import PARSER_VERSION, ParsePort, ParseResult
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_NORMALIZED_IMAGE_BYTES = 16 * 1024 * 1024


class ParsedFile(BaseModel):
    source_file_id: UUID
    source_sha256: str
    parser_version: str
    artifact_file_id: UUID
    text: str
    metadata: dict[str, str | int]


class FileInputService:
    def __init__(self, database: Database, files: FileService, parser: ParsePort, objects: FileObjects) -> None:
        self._database, self._files, self._parser = database, files, parser
        self._objects = objects

    async def prepare(self, principal: Principal, file_id: UUID) -> PreparedAttachment:
        async with self._database.sessions() as session:
            info, content = await self._files.read(session, principal, file_id)
            if info.source_file_id is not None:
                raise AppError('derived_file_immutable', '请解析原文件', 422)
            cached = await self._files.cached_artifact(session, principal, file_id, PARSER_VERSION)
        artifact_id = None
        if cached is not None:
            artifact_id, key = cached
            async with self._database.sessions() as session:
                raw = await self._files.artifact_content(session, principal, artifact_id)
            result = ParseResult.model_validate_json(raw)
            if result.parser_version != PARSER_VERSION or result.source_sha256 != info.sha256:
                raise AppError('file_integrity_error', '派生产物缓存校验失败', 503)
        else:
            result = await self._parser.parse(content, filename=info.original_filename)
            result = result.model_copy(update={'source_sha256': info.sha256})
            raw = result.model_dump_json().encode()
            key = await self._files.storage.put(raw)
            async with self._objects.registration(key) as session:
                source = await self._files.require(session, principal, file_id, active=True, lock=True)
                if (
                    source.sha256 != info.sha256
                    or source.version != info.version
                    or source.acl_version != info.acl_version
                ):
                    raise AppError('file_snapshot_changed', '附件已变化，请重试', 409)
                cached = await self._files.cached_artifact(session, principal, file_id, PARSER_VERSION)
                if cached is None:
                    artifact = await self._files.create_artifact(
                        session, source_id=file_id, content=raw, storage_key=key, parser_version=PARSER_VERSION
                    )
                    artifact_id = artifact.id
                else:
                    artifact_id, _ = cached
        return PreparedAttachment(
            summary=AttachmentSummary(
                owner_id=info.owner_id,
                file_id=info.id,
                original_filename=info.original_filename,
                mime=info.mime,
                size=info.size,
                sha256=info.sha256,
                artifact_file_id=artifact_id,
                parser_version=PARSER_VERSION,
            ),
            version=info.version,
            acl_version=info.acl_version,
            text=result.text,
            image_data=result.image_data,
        )

    async def parse(self, principal: Principal, file_id: UUID) -> ParsedFile:
        item = await self.prepare(principal, file_id)
        assert item.summary.artifact_file_id is not None
        async with self._database.sessions() as session:
            cached = await self._files.cached_artifact(session, principal, file_id, PARSER_VERSION)
        assert cached is not None
        async with self._database.sessions() as session:
            result = ParseResult.model_validate_json(await self._files.artifact_content(session, principal, cached[0]))
        return ParsedFile(
            source_file_id=file_id,
            source_sha256=item.summary.sha256,
            parser_version=PARSER_VERSION,
            artifact_file_id=item.summary.artifact_file_id,
            text=item.text,
            metadata=result.metadata,
        )

    async def from_artifact(self, principal: Principal, file_id: UUID, artifact_id: UUID) -> PreparedAttachment:
        async with self._database.sessions() as session:
            info = await self._files.get(session, principal, file_id)
            artifact = await self._files.get(session, principal, artifact_id)
            if artifact.source_file_id != file_id:
                raise AppError('file_integrity_error', '解析产物与源文件不匹配', 503)
            raw = await self._files.artifact_content(session, principal, artifact_id)
        result = ParseResult.model_validate_json(raw)
        if result.source_sha256 != info.sha256 or result.parser_version != artifact.parser_version:
            raise AppError('file_integrity_error', '解析产物身份不匹配', 503)
        return PreparedAttachment(
            summary=AttachmentSummary(
                owner_id=info.owner_id,
                file_id=info.id,
                original_filename=info.original_filename,
                mime=info.mime,
                size=info.size,
                sha256=info.sha256,
                artifact_file_id=artifact_id,
                parser_version=result.parser_version,
            ),
            version=info.version,
            acl_version=info.acl_version,
            text=result.text,
            image_data=result.image_data,
        )

    async def parse_artifact(self, principal: Principal, file_id: UUID, artifact_id: UUID) -> ParsedFile:
        item = await self.from_artifact(principal, file_id, artifact_id)
        async with self._database.sessions() as session:
            raw = await self._files.artifact_content(session, principal, artifact_id)
        result = ParseResult.model_validate_json(raw)
        return ParsedFile(
            source_file_id=file_id,
            source_sha256=item.summary.sha256,
            parser_version=result.parser_version,
            artifact_file_id=artifact_id,
            text=result.text,
            metadata=result.metadata,
        )

    async def store_parsed(
        self,
        principal: Principal,
        file_id: UUID,
        result: ParseResult,
        *,
        source_sha256: str,
        source_version: int,
        source_acl_version: int,
    ) -> UUID:
        result = result.model_copy(update={'source_sha256': source_sha256})
        raw = result.model_dump_json().encode()
        key = await self._files.storage.put(raw)
        async with self._objects.registration(key) as session:
            source = await self._files.require(session, principal, file_id, active=True, lock=True)
            if (
                source.sha256 != source_sha256
                or source.version != source_version
                or source.acl_version != source_acl_version
            ):
                raise AppError('file_snapshot_changed', '文件或访问权限已变化，请重新提交', 409)
            cached = await self._files.cached_artifact(session, principal, file_id, result.parser_version)
            if cached is not None:
                return cached[0]
            artifact = await self._files.create_artifact(
                session, source_id=file_id, content=raw, storage_key=key, parser_version=result.parser_version
            )
            return artifact.id

    async def prepare_many(self, principal: Principal, file_ids: list[UUID]) -> tuple[PreparedAttachment, ...]:
        if len(set(file_ids)) != len(file_ids):
            raise AppError('invalid_attachments', '附件不能重复', 422)
        if not file_ids:
            return ()
        # Authorize server-owned metadata and bound the batch before reading or parsing bytes.
        total_bytes = 0
        async with self._database.sessions() as session:
            for file_id in file_ids:
                info = await self._files.get(session, principal, file_id)
                total_bytes += info.size
                if total_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
                    raise AppError('attachments_too_large', '每条消息附件合计不能超过50MB', 422)
        result = []
        image_bytes = 0
        for file_id in file_ids:
            item = await self.prepare(principal, file_id)
            if item.image_data is not None:
                image_bytes += len(item.image_data.encode())
                if image_bytes > MAX_NORMALIZED_IMAGE_BYTES:
                    raise AppError('attachment_images_too_large', '安全图像数据合计最多16MiB', 422)
            result.append(item)
        return tuple(result)
