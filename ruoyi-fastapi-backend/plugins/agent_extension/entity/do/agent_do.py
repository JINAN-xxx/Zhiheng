from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.dialects.postgresql import JSONB

from config.database import Base


class AgentConfiguration(Base):
    __tablename__ = 'agent_configuration'
    __table_args__ = (
        CheckConstraint("status in ('draft', 'published')", name='ck_agent_configuration_status'),
        CheckConstraint('version > 0', name='ck_agent_configuration_version'),
        CheckConstraint(
            'published_version is null or published_version > 0', name='ck_agent_configuration_published_version'
        ),
        CheckConstraint('temperature >= 0 and temperature <= 2', name='ck_agent_configuration_temperature'),
        CheckConstraint('max_tokens > 0', name='ck_agent_configuration_max_tokens'),
        Index('idx_agent_configuration_owner_status', 'owner_user_id', 'status'),
    )

    agent_id = Column(String(36), primary_key=True, nullable=False)
    owner_user_id = Column(BigInteger, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=False, default='', server_default='')
    model_id = Column(String(36), nullable=False)
    system_prompt = Column(Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=False, default='')
    welcome_message = Column(String(2000), nullable=False, default='', server_default='')
    skill_slugs = Column(JSON().with_variant(JSONB(), 'postgresql'), nullable=False, default=list)
    knowledge_base_ids = Column(JSON().with_variant(JSONB(), 'postgresql'), nullable=False, default=list)
    application_id = Column(String(36), nullable=True)
    temperature = Column(Float, nullable=False, default=0.7)
    max_tokens = Column(Integer, nullable=False, default=2048)
    status = Column(String(16), nullable=False, default='draft', server_default='draft')
    version = Column(Integer, nullable=False, default=1, server_default='1')
    published_config = Column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), 'postgresql'), nullable=True
    )
    published_version = Column(Integer, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
