from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from config.database import Base


class AiResourceBinding(Base):
    """Platform资源与运行时引擎绑定表。"""

    __tablename__ = 'ai_resource_binding'
    __table_args__ = (
        UniqueConstraint('resource_type', 'resource_id', name='uk_ai_resource_binding_resource'),
        CheckConstraint(
            "binding_status in ('active', 'disabled')",
            name='ck_ai_resource_binding_status',
        ),
        CheckConstraint("resource_type = 'model'", name='ck_ai_resource_binding_resource_type'),
        CheckConstraint("engine_type = 'litellm'", name='ck_ai_resource_binding_engine_type'),
        CheckConstraint('version > 0', name='ck_ai_resource_binding_version'),
        Index('idx_ai_resource_binding_engine', 'engine_type', 'engine_resource_id'),
        {'comment': 'Platform资源绑定表'},
    )

    binding_id = Column(String(36), primary_key=True, nullable=False, comment='绑定ID')
    resource_type = Column(String(32), nullable=False, comment='资源类型')
    resource_id = Column(
        String(36),
        ForeignKey('ai_model_catalog.model_id', ondelete='RESTRICT'),
        nullable=False,
        comment='平台资源ID',
    )
    engine_type = Column(String(32), nullable=False, comment='运行时引擎类型')
    engine_resource_id = Column(String(255), nullable=False, comment='引擎侧资源标识')
    binding_status = Column(String(16), nullable=False, server_default='active', comment='绑定状态')
    engine_metadata = Column(
        JSON().with_variant(JSONB(), 'postgresql'),
        nullable=False,
        default=dict,
        comment='引擎元数据JSON',
    )
    version = Column(Integer, nullable=False, default=1, server_default='1', comment='乐观锁版本')
    create_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    create_time = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    update_time = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')
