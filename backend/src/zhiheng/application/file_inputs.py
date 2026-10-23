"""Prepare untrusted attachment bytes outside locked admission transactions."""

from uuid import UUID

from pydantic import BaseModel

from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import AttachmentSummary, FileService, PreparedAttachment
from zhiheng.modules.knowledge.public import PARSER_VERSION, ParsePort, ParseResult
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_ATTACHMENTS = 15
MAX_IMAGES = 10
MAX_DOCUMENTS = 5
MAX_NORMALIZED_IMAGE_BYTES = 16 * 1024 * 1024
MAX_ATTACHMENT_CHARACTERS = 40000
MAX_TOTAL_ATTACHMENT_CHARACTERS = 60000


class ParsedFile(BaseModel):
    source_file_id: UUID
    source_sha256: str
    parser_version: str
    artifact_file_id: UUID
    text: str
    metadata: dict[str, str | int]


class FileInputService:
    def __init__(self, database: Database, files: FileService, parser: ParsePort) -> None:
        self._database, self._files, self._parser = database, files, parser

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
            try:
                async with self._database.sessions.begin() as session:
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
            except BaseException:
                await self._files.storage.delete(key)
                raise
            if cached is not None:
                await self._files.storage.delete(key)
        return PreparedAttachment(
            summary=AttachmentSummary(
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

    async def prepare_many(self, principal: Principal, file_ids: list[UUID]) -> tuple[PreparedAttachment, ...]:
        if len(file_ids) > MAX_ATTACHMENTS or len(set(file_ids)) != len(file_ids):
            raise AppError('invalid_attachments', '最多10张图片和5个文档，附件不能重复', 422)
        result = []
        total = 0
        images = documents = image_bytes = 0
        for file_id in file_ids:
            item = await self.prepare(principal, file_id)
            if item.image_data is not None:
                images += 1
                image_bytes += len(item.image_data.encode())
                if images > MAX_IMAGES or image_bytes > MAX_NORMALIZED_IMAGE_BYTES:
                    raise AppError('attachment_images_too_large', '最多10张图片，安全图像数据合计最多16MiB', 422)
            else:
                documents += 1
                if documents > MAX_DOCUMENTS:
                    raise AppError('too_many_documents', '最多5个文档附件', 422)
                total += len(item.text)
                if len(item.text) > MAX_ATTACHMENT_CHARACTERS or total > MAX_TOTAL_ATTACHMENT_CHARACTERS:
                    raise AppError('attachment_text_too_long', '单附件最多40000字符，合计最多60000字符', 422)
            result.append(item)
        return tuple(result)
