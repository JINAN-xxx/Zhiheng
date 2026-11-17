"""Existing document_parse_settings table, owned by the file domain."""

from sqlalchemy import Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base


class ParsingConfiguration(Base):
    __tablename__ = 'document_parse_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
