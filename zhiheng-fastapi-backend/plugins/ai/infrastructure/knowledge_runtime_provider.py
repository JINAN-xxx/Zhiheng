import importlib
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import AsyncSessionLocal
from module_admin.dao.user_dao import UserDao
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine_for_app
from plugins.ai.service.model_service import ModelService

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def owner_can_query_knowledge(
    owner_user_id: int,
    *,
    session_factory: SessionFactory = AsyncSessionLocal,
    user_dao: Any = UserDao,
) -> bool:
    """每次检索重新校验所属用户，权限撤销立即影响已发布智能体。"""
    async with session_factory() as session:
        profile = await user_dao.get_user_by_id(session, owner_user_id)
    user = profile.get('user_basic_info') if profile else None
    if user is None or str(user.status) != '0' or str(user.del_flag) != '0':
        return False
    if any(role.role_id == 1 for role in profile.get('user_role_info', ())):
        return True
    return any(menu.perms in {'*:*:*', 'ai:knowledge:query'} for menu in profile.get('user_menu_info', ()))


def create_knowledge_runtime(
    app: FastAPI,
    *,
    data_dir: Path | None = None,
    runtime_factory: Callable[..., Any] | None = None,
    session_factory: SessionFactory = AsyncSessionLocal,
) -> Any:
    """创建知识库运行时；实际模型调用使用独立数据库会话和平台连接池。"""

    @asynccontextmanager
    async def model_service_factory() -> AsyncIterator[ModelService]:
        async with session_factory() as session:
            yield ModelService(session, get_model_runtime_engine_for_app(app))

    async def owner_authorizer(owner_user_id: int) -> bool:
        return await owner_can_query_knowledge(owner_user_id, session_factory=session_factory)

    if runtime_factory is None:
        runtime_factory = importlib.import_module('plugins.ai.knowledge.runtime').KnowledgeRuntime
    return runtime_factory(
        data_dir=data_dir or Path(os.getenv('KNOWLEDGE_DATA_DIR', 'data/knowledge')).resolve(),
        model_service_factory=model_service_factory,
        owner_authorizer=owner_authorizer,
    )
