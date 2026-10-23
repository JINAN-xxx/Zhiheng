from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ConversationInfo(BaseModel):
    id: UUID
    title: str
    created_at: datetime


class MessageInfo(BaseModel):
    id: UUID
    role: Literal['user', 'assistant']
    content: str
    run_id: UUID
    created_at: datetime
