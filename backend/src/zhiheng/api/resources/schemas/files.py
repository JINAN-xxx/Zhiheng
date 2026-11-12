"""files resource wire DTOs."""

from pydantic import BaseModel

from zhiheng.modules.files.public import FileInfo


class ResourceFilePage(BaseModel):
    items: list[FileInfo]
    next_cursor: str | None
    has_more: bool
