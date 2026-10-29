from uuid import UUID

from pydantic import BaseModel


class IdentityInfo(BaseModel):
    id: UUID
    username: str


class IdentityDirectoryEntry(BaseModel):
    """Minimal account directory for authorized business administration."""

    id: UUID
    username: str
    display_name: str
    is_active: bool


class IdentityDirectoryPage(BaseModel):
    items: list[IdentityDirectoryEntry]
    total: int
    page: int
    page_size: int
