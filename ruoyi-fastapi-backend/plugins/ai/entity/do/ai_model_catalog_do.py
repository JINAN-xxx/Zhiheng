from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, JSON, String

from config.database import Base


class AiModelCatalog(Base):
    """Platform模型目录表。"""

    __tablename__ = 'ai_model_catalog'
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status in ('draft', 'active', 'disabled')",
            name='ck_ai_model_catalog_lifecycle_status',
        ),
        CheckConstraint(
            'context_window is null or context_window > 0',
            name='ck_ai_model_catalog_context_window',
        ),
        CheckConstraint(
            'max_output_tokens is null or max_output_tokens > 0',
            name='ck_ai_model_catalog_max_output_tokens',
        ),
        {'comment': 'Platform模型目录表'},
    )

    model_id = Column(String(36), primary_key=True, nullable=False, comment='平台模型ID')
    model_code = Column(String(100), nullable=False, unique=True, comment='平台模型编码')
    display_name = Column(String(100), nullable=False, comment='模型展示名称')
    model_type = Column(String(32), nullable=False, comment='模型类型')
    capabilities = Column(JSON, nullable=False, default=dict, comment='模型能力JSON')
    context_window = Column(Integer, nullable=True, comment='上下文窗口大小')
    max_output_tokens = Column(Integer, nullable=True, comment='最大输出Token数')
    default_parameters = Column(JSON, nullable=False, default=dict, comment='默认调用参数JSON')
    lifecycle_status = Column(String(16), nullable=False, server_default='draft', comment='生命周期状态')
    published_at = Column(DateTime, nullable=True, comment='发布时间')
    create_by = Column(String(64), nullable=False, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=False, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')
