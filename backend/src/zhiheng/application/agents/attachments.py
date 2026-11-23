"""Assistant-scoped attachment preparation and current access checks."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.files.inputs import FileInputService
from zhiheng.modules.files.public import AttachmentSummary, FileService, PreparedAttachment
from zhiheng.modules.models.public import ModelBinding, ModelImage, ModelMessage
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class AssistantAttachments:
    def __init__(self, inputs: FileInputService, files: FileService) -> None:
        self.inputs, self.files = inputs, files

    async def prepare(self, principal: Principal, file_ids: Sequence[UUID]) -> tuple[PreparedAttachment, ...]:
        return await self.inputs.prepare_many(principal, list(file_ids))

    async def validate(
        self, session: AsyncSession, principal: Principal, records: list[AttachmentSummary], binding: ModelBinding
    ) -> None:
        for item in sorted(records, key=lambda row: row.file_id):
            current = await self.files.require(session, principal, item.file_id, active=True)
            if current.sha256 != item.sha256:
                raise AppError('file_snapshot_changed', '附件内容已变化，请重新上传', 409)
            if item.artifact_file_id:
                artifact = await self.files.require(session, principal, item.artifact_file_id, active=True)
                if artifact.source_file_id != item.file_id:
                    raise AppError('file_snapshot_changed', '附件解析记录已变化，请重新上传', 409)
            if item.mime.startswith('image/') and not binding.vision_enabled:
                raise AppError('unsupported_capability', '当前助手模型不支持图片，请使用支持视觉的模型', 422)

    @staticmethod
    def message(text: str, attachments: tuple[PreparedAttachment, ...]) -> ModelMessage:
        extra = ''.join(
            f'\n\n[Attachment: {item.summary.original_filename}]\n'
            + (f'[解析提示：{item.summary.parse_warning}]\n' if item.summary.parse_warning else '')
            + item.text
            for item in attachments
            if item.image_data is None
        )
        images = tuple(ModelImage(item.image_data) for item in attachments if item.image_data is not None)
        return ModelMessage('user', text + extra, images)

    async def retain(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        attachments: tuple[PreparedAttachment, ...],
    ) -> None:
        await self.files.references.attach(
            session, principal, attachments, resource_type='agent_chat', resource_id=conversation_id
        )
