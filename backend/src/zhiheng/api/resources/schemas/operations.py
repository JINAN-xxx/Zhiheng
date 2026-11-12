"""operations resource wire DTOs."""

from pydantic import BaseModel

from zhiheng.application.operations.contracts import OperationInfo


class OperationPage(BaseModel):
    items: list[OperationInfo]
    next_cursor: str | None
    has_more: bool
