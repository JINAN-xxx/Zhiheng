from datetime import datetime
from typing import Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from config.database import Base

ChatSessionStatus = Literal['active', 'archived']
ChatMessageRole = Literal['user', 'assistant']
ChatMessageStatus = Literal['complete', 'interrupted', 'cancelled']
ChatRunStatus = Literal['pending', 'running', 'completed', 'cancelled', 'interrupted', 'failed']


class AiChatSession(Base):
    """Platform 对话会话。"""

    __tablename__ = 'ai_chat_session'
    __table_args__ = (
        CheckConstraint("status in ('active', 'archived')", name='ck_ai_chat_session_status'),
        Index('idx_ai_chat_session_user_updated', 'user_id', 'updated_at'),
        {'comment': 'Platform 对话会话表'},
    )

    session_id = Column(String(36), primary_key=True, nullable=False, comment='Platform Session ID')
    user_id = Column(BigInteger, nullable=False, comment='平台用户ID')
    model_id = Column(
        String(36),
        ForeignKey('ai_model_catalog.model_id', ondelete='RESTRICT'),
        nullable=False,
        comment='锁定的 Platform Model ID',
    )
    title = Column(String(200), nullable=False, server_default=text("''"), comment='会话标题')
    status = Column(String(16), nullable=False, server_default='active', comment='会话状态')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiChatMessage(Base):
    """Platform 对话消息。"""

    __tablename__ = 'ai_chat_message'
    __table_args__ = (
        UniqueConstraint('session_id', 'client_message_id', name='uk_ai_chat_message_client'),
        CheckConstraint("role in ('user', 'assistant')", name='ck_ai_chat_message_role'),
        CheckConstraint(
            "status in ('complete', 'interrupted', 'cancelled')",
            name='ck_ai_chat_message_status',
        ),
        Index('idx_ai_chat_message_session_created', 'session_id', 'created_at'),
        {'comment': 'Platform 对话消息表'},
    )

    message_id = Column(String(36), primary_key=True, nullable=False, comment='Platform Message ID')
    session_id = Column(
        String(36),
        ForeignKey('ai_chat_session.session_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Session ID',
    )
    client_message_id = Column(String(100), nullable=False, comment='客户端幂等消息ID')
    role = Column(String(16), nullable=False, comment='消息角色')
    content = Column(Text, nullable=False, comment='消息正文')
    reasoning_content = Column(Text, nullable=True, comment='推理内容')
    images = Column(
        JSON().with_variant(JSONB(), 'postgresql'),
        nullable=False,
        default=list,
        comment='Platform 文件引用JSON',
    )
    skill_snapshots = Column(
        JSON().with_variant(JSONB(), 'postgresql'),
        nullable=False,
        default=list,
        comment='当前消息使用的技能名称快照',
    )
    knowledge_base_ids = Column(
        JSON().with_variant(JSONB(), 'postgresql'),
        nullable=False,
        default=list,
        comment='当前消息选择的知识库ID',
    )
    status = Column(String(16), nullable=False, comment='消息状态')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiChatRun(Base):
    """Platform 对话运行记录。"""

    __tablename__ = 'ai_chat_run'
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'completed', 'cancelled', 'interrupted', 'failed')",
            name='ck_ai_chat_run_status',
        ),
        Index('idx_ai_chat_run_session_created', 'session_id', 'created_at'),
        Index('idx_ai_chat_run_status', 'status'),
        {'comment': 'Platform 对话运行表'},
    )

    run_id = Column(String(36), primary_key=True, nullable=False, comment='Platform Run ID')
    session_id = Column(
        String(36),
        ForeignKey('ai_chat_session.session_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Session ID',
    )
    model_id = Column(
        String(36),
        ForeignKey('ai_model_catalog.model_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Model 快照',
    )
    binding_id = Column(
        String(36),
        ForeignKey('ai_resource_binding.binding_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Binding 快照',
    )
    engine_type = Column(String(32), nullable=False, comment='运行时引擎类型')
    engine_model_snapshot = Column(String(255), nullable=False, comment='引擎模型快照')
    engine_request_id = Column(String(255), nullable=True, comment='引擎请求ID')
    trace_id = Column(String(128), nullable=True, comment='Platform Trace ID')
    status = Column(String(16), nullable=False, server_default='pending', comment='运行状态')
    input_tokens = Column(Integer, nullable=True, comment='输入Token数')
    output_tokens = Column(Integer, nullable=True, comment='输出Token数')
    reasoning_tokens = Column(Integer, nullable=True, comment='推理Token数')
    cache_read_tokens = Column(Integer, nullable=True, comment='缓存读取Token数')
    cache_write_tokens = Column(Integer, nullable=True, comment='缓存写入Token数')
    raw_cost = Column(Numeric(20, 10), nullable=True, comment='引擎原始成本')
    error_code = Column(String(64), nullable=True, comment='Platform 脱敏错误码')
    error_message = Column(String(500), nullable=True, comment='Platform 脱敏错误文案')
    started_at = Column(DateTime, nullable=True, comment='开始时间')
    finished_at = Column(DateTime, nullable=True, comment='结束时间')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiChatPreferences(Base):
    """Platform 对话用户偏好。"""

    __tablename__ = 'ai_chat_preferences'
    __table_args__ = ({'comment': 'Platform 对话用户偏好表'},)

    preference_id = Column(
        BigInteger().with_variant(Integer(), 'sqlite'),
        Identity(),
        primary_key=True,
        autoincrement=True,
        nullable=False,
        comment='偏好ID',
    )
    user_id = Column(BigInteger, nullable=False, unique=True, comment='平台用户ID')
    temperature = Column(Float, nullable=True, comment='默认温度')
    add_history = Column(Boolean, nullable=False, default=True, server_default=text('true'), comment='是否附带历史')
    history_message_count = Column(
        Integer,
        nullable=False,
        default=20,
        server_default=text('20'),
        comment='历史消息数量',
    )
    system_prompt = Column(Text, nullable=True, comment='系统提示词')
    metrics_visible = Column(Boolean, nullable=False, default=True, server_default=text('true'), comment='是否展示指标')
    vision_enabled = Column(Boolean, nullable=False, default=True, server_default=text('true'), comment='是否启用视觉')
    image_max_size_mb = Column(
        Integer,
        nullable=False,
        default=10,
        server_default=text('10'),
        comment='图片大小上限MB',
    )
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')
