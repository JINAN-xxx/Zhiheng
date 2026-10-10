"""应用绑定条件的只读查询；会话与事务生命周期由调用方负责。"""

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao


async def is_active_owned_application(db: AsyncSession, application_id: str, owner_user_id: int) -> bool:
    """检查未删除、已启用且属于指定所有者的应用，不返回持久化对象。

    所有者由调用业务明确传入；此结果不替代密钥认证、模型授权或额度检查。
    """
    application = await AiDeveloperApiDao.get_application_by_id(db, application_id)
    return application is not None and application.owner_user_id == owner_user_id and application.status == 'active'
