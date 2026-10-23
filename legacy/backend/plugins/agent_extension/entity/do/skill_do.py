from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from config.database import Base


class AgentSkill(Base):
    """平台共享技能。"""

    __tablename__ = 'agent_skill'
    __table_args__ = (
        CheckConstraint("status in ('enabled', 'disabled')", name='ck_agent_skill_status'),
        CheckConstraint("source_type in ('builtin', 'upload')", name='ck_agent_skill_source_type'),
        CheckConstraint('lock_version > 0', name='ck_agent_skill_lock_version'),
        Index('idx_agent_skill_status', 'status'),
        {'comment': '平台共享技能表'},
    )

    skill_id = Column(String(36), primary_key=True, nullable=False, comment='技能ID')
    skill_slug = Column(String(100), nullable=False, unique=True, comment='技能稳定标识')
    skill_name = Column(String(100), nullable=False, comment='技能展示名称')
    description = Column(String(500), nullable=False, server_default='', comment='技能说明')
    version = Column(String(64), nullable=False, server_default='1.0.0', comment='技能内容版本')
    status = Column(String(16), nullable=False, server_default='enabled', comment='技能状态')
    source_type = Column(String(16), nullable=False, server_default='upload', comment='技能来源')
    storage_path = Column(String(512), nullable=False, comment='相对存储路径')
    content_hash = Column(String(64), nullable=False, comment='内容摘要')
    metadata_json = Column(
        JSON().with_variant(JSONB(), 'postgresql'),
        nullable=False,
        default=dict,
        comment='扩展元数据',
    )
    lock_version = Column(Integer, nullable=False, default=1, server_default='1', comment='乐观锁版本')
    created_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')
