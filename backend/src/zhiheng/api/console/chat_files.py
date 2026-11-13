"""Chat uploads have a server-owned temporary lifecycle until message admission."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency
from zhiheng.modules.files.public import FileInfo

router = APIRouter(prefix='/api/chat/attachments', tags=['chat'])


@router.post('', status_code=201, openapi_extra={'x-transport-policy': 'binary_file'})
async def upload(
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> FileInfo:
    return await container.file_commands.upload(
        principal, filename=filename, content=await request.body(), purpose='chat_attachment'
    )
