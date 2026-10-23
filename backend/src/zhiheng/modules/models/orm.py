from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base


class ModelConnection(Base):
    __tablename__ = 'model_connections'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(String(2048))
    encrypted_api_key: Mapped[str] = mapped_column(Text)


class ModelDefinition(Base):
    __tablename__ = 'models'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey('model_connections.id'))
    name: Mapped[str] = mapped_column(String(80))
    upstream_model: Mapped[str] = mapped_column(String(200))
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
