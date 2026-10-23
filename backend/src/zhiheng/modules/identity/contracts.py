from uuid import UUID

from pydantic import BaseModel


class IdentityInfo(BaseModel):
    id: UUID
    username: str
