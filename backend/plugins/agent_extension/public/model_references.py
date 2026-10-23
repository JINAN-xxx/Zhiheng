"""持久模型引用查询，停用插件后仍可用于数据完整性检查。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTaskMember


async def has_model_references(db: AsyncSession, model_id: str) -> bool:
    """包含草稿、发布快照、已移除成员和历史运行；不管理调用方事务。

    删除方已经锁定模型行。锁定读取避免 MySQL 旧事务快照漏掉刚提交的引用。
    """
    queries = (
        select(AgentConfiguration.agent_id).where(
            or_(
                AgentConfiguration.model_id == model_id,
                AgentConfiguration.published_config['modelId'].as_string() == model_id,
                AgentConfiguration.published_config['model_id'].as_string() == model_id,
            )
        ),
        select(AgentWorkTaskMember.task_id).where(
            or_(
                AgentWorkTaskMember.config_snapshot['modelId'].as_string() == model_id,
                AgentWorkTaskMember.config_snapshot['model_id'].as_string() == model_id,
            )
        ),
        select(AgentWorkMessage.message_id).where(
            or_(
                AgentWorkMessage.audit_snapshot['modelId'].as_string() == model_id,
                AgentWorkMessage.audit_snapshot['model_id'].as_string() == model_id,
            )
        ),
    )
    for query in queries:
        if await db.scalar(query.limit(1).with_for_update()) is not None:
            return True
    return False
