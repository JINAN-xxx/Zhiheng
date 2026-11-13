"""One registration and lifecycle transaction for Console and resource adapters."""

from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.application.files.objects import FileObjects
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileInfo, FileService, file_semantics, validate_filename
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_FILE_BYTES = 20 * 1024 * 1024


class FileCommands:
    def __init__(
        self, database: Database, files: FileService, access: ResourceAccessService, objects: FileObjects
    ) -> None:
        self.database, self.files, self.access = database, files, access
        self.objects = objects

    async def upload(
        self,
        principal: Principal,
        *,
        filename: str,
        content: bytes,
        purpose: str = 'general',
        caller: CallerContext | None = None,
        idempotency_key: UUID | None = None,
    ) -> FileInfo:
        if purpose in {'knowledge_document', 'knowledge_material', 'knowledge_upload'} or (
            caller is not None and purpose == 'chat_attachment'
        ):
            raise AppError('reserved_file_purpose', '业务资料只能通过对应业务入口添加', 422)
        validate_filename(filename)
        if len(content) > MAX_FILE_BYTES:
            raise AppError('request_too_large', '文件超过20MiB', 413)
        if not content:
            raise AppError('invalid_file_size', '文件为空', 422)
        # The native detector, rather than the untrusted multipart MIME header, defines effective MIME.
        semantics = file_semantics(filename, content, purpose)
        storage_key = await self.files.storage.put(content)
        async with self.objects.registration(storage_key) as session:
            admission = None
            if caller is not None:
                if idempotency_key is None:
                    raise AppError('invalid_idempotency_key', 'UUID请求键为必填', 422)
                caller = await self.access.current(session, caller, 'files:write')
                principal = self.access.personal(caller.principal)
                admission = await self.access.begin_request(
                    session,
                    caller,
                    'files.create',
                    idempotency_key,
                    semantics,
                    scope='files:write',
                )
                if admission.replayed:
                    if admission.snapshot is None:
                        raise AppError('request_pending', '原请求尚未登记完成', 409)
                    result = FileInfo.model_validate(admission.snapshot)
                else:
                    result = await self.files.create(
                        session,
                        principal,
                        filename=filename,
                        content=content,
                        storage_key=storage_key,
                        purpose=purpose,
                    )
            else:
                result = await self.files.create(
                    session,
                    principal,
                    filename=filename,
                    content=content,
                    storage_key=storage_key,
                    purpose=purpose,
                )
            if admission is not None and not admission.replayed:
                assert caller is not None
                await self.access.autobind(session, caller, 'file', result.id)
                await self.access.requests.complete(
                    session,
                    admission.id,
                    result_kind='file',
                    result_id=result.id,
                    snapshot=result.model_dump(mode='json'),
                    resources=({'kind': 'file', 'id': str(result.id), 'action': 'write'},),
                )
        return result

    async def remove(
        self,
        principal: Principal,
        file_id: UUID,
        expected_version: int,
        *,
        restore: bool = False,
        caller: CallerContext | None = None,
    ) -> FileInfo:
        async with self.database.sessions.begin() as session:
            if caller is not None:
                await self.access.require(session, caller, 'file', file_id, 'write' if restore else 'delete')
                principal = self.access.personal(caller.principal)
                current = await self.files.get(session, principal, file_id)
                if current.version != expected_version:
                    raise AppError('precondition_failed', '文件版本已改变', 412)
            return await self.files.remove(session, principal, file_id, expected_version, restore=restore)
