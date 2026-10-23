from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.dialects.postgresql import JSONB

from config.database import Base

WorkText = Text().with_variant(MEDIUMTEXT(), 'mysql')


class AgentWorkTask(Base):
    """用户拥有的多 Agent 工作任务。"""

    __tablename__ = 'agent_work_task'
    __table_args__ = (
        UniqueConstraint(
            'owner_user_id',
            'create_client_message_id',
            name='uk_agent_work_task_create_client',
        ),
        CheckConstraint("status in ('active', 'completed', 'archived')", name='ck_agent_work_task_status'),
        CheckConstraint('version > 0', name='ck_agent_work_task_version'),
        Index('idx_agent_work_task_owner_status_updated', 'owner_user_id', 'status', 'updated_at'),
        {'comment': 'Agent 工作任务表'},
    )

    task_id = Column(String(36), primary_key=True, nullable=False, comment='工作任务ID')
    owner_user_id = Column(BigInteger, nullable=False, comment='任务所有者用户ID')
    create_client_message_id = Column(String(100), nullable=False, comment='首次创建幂等消息ID')
    create_request_hash = Column(String(64), nullable=False, default='', server_default='')
    current_run_id = Column(String(36), nullable=True)
    title = Column(String(100), nullable=False, comment='任务标题')
    goal = Column(WorkText, nullable=False, comment='任务目标')
    status = Column(String(16), nullable=False, default='active', server_default='active', comment='任务状态')
    active_agent_id = Column(String(36), nullable=True, comment='当前 Agent ID')
    version = Column(Integer, nullable=False, default=1, server_default='1', comment='乐观锁版本')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AgentWorkTaskMember(Base):
    """工作任务的 Agent 配置快照。"""

    __tablename__ = 'agent_work_task_member'
    __table_args__ = (
        CheckConstraint('published_version > 0', name='ck_agent_work_task_member_published_version'),
        Index('idx_agent_work_task_member_task_removed', 'task_id', 'removed_at'),
        {'comment': 'Agent 工作任务成员表'},
    )

    task_id = Column(
        String(36),
        ForeignKey('agent_work_task.task_id', ondelete='RESTRICT'),
        primary_key=True,
        nullable=False,
        comment='工作任务ID',
    )
    agent_id = Column(String(36), primary_key=True, nullable=False, comment='Agent ID')
    agent_name_snapshot = Column(String(100), nullable=False, comment='Agent 名称快照')
    published_version = Column(Integer, nullable=False, comment='已发布版本快照')
    config_snapshot = Column(
        JSON().with_variant(JSONB(), 'postgresql'),
        nullable=False,
        default=dict,
        comment='Agent 已发布配置快照',
    )
    added_at = Column(DateTime, nullable=False, default=datetime.now, comment='加入时间')
    removed_at = Column(DateTime, nullable=True, comment='移除时间')


class AgentWorkMessage(Base):
    """工作任务中的用户消息和 Agent 运行结果。"""

    __tablename__ = 'agent_work_message'
    __table_args__ = (
        UniqueConstraint('task_id', 'client_message_id', name='uk_agent_work_message_client'),
        UniqueConstraint('run_id', name='uk_agent_work_message_run'),
        UniqueConstraint('task_id', 'sequence', name='uk_agent_work_message_sequence'),
        CheckConstraint("role in ('user', 'assistant')", name='ck_agent_work_message_role'),
        CheckConstraint(
            'run_status is null or run_status in '
            "('pending', 'streaming', 'completed', 'cancelled', 'interrupted', 'failed')",
            name='ck_agent_work_message_run_status',
        ),
        CheckConstraint(
            'published_version is null or published_version > 0',
            name='ck_agent_work_message_published_version',
        ),
        CheckConstraint(
            "(role = 'user' and client_message_id is not null and reply_to_message_id is null "
            "and reasoning_content = '' "
            'and agent_id is null and agent_name_snapshot is null and published_version is null '
            'and run_id is null and run_status is null and usage_snapshot is null and error_snapshot is null) '
            "or (role = 'assistant' and client_message_id is null and reply_to_message_id is not null "
            'and agent_id is not null and agent_name_snapshot is not null and published_version is not null '
            'and run_id is not null and run_status is not null)',
            name='ck_agent_work_message_shape',
        ),
        Index('idx_agent_work_message_task_created', 'task_id', 'created_at'),
        Index('idx_agent_work_message_task_run_status', 'task_id', 'run_status'),
        {'comment': 'Agent 工作任务消息表'},
    )

    message_id = Column(String(36), primary_key=True, nullable=False, comment='工作消息ID')
    sequence = Column(Integer, nullable=False, default=1)
    task_id = Column(
        String(36),
        ForeignKey('agent_work_task.task_id', ondelete='RESTRICT'),
        nullable=False,
        comment='工作任务ID',
    )
    client_message_id = Column(String(100), nullable=True, comment='客户端幂等消息ID')
    reply_to_message_id = Column(String(36), nullable=True, comment='回复的用户消息ID')
    role = Column(String(16), nullable=False, comment='消息角色')
    content = Column(WorkText, nullable=False, comment='消息正文')
    reasoning_content = Column(
        WorkText,
        nullable=False,
        default='',
        server_default=text("('')"),
        comment='推理内容',
    )
    agent_id = Column(String(36), nullable=True, comment='Agent ID 快照')
    agent_name_snapshot = Column(String(100), nullable=True, comment='Agent 名称快照')
    published_version = Column(Integer, nullable=True, comment='Agent 已发布版本快照')
    run_id = Column(String(36), nullable=True, comment='运行ID')
    run_status = Column(String(16), nullable=True, comment='运行状态')
    usage_snapshot = Column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), 'postgresql'),
        nullable=True,
        comment='用量快照',
    )
    error_snapshot = Column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), 'postgresql'),
        nullable=True,
        comment='脱敏错误快照',
    )
    audit_snapshot = Column(JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), 'postgresql'), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
