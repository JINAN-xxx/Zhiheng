import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.exc import SQLAlchemyError

from exceptions.exception import ServiceException
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.infrastructure.litellm_management_errors import LiteLLMManagementError

ACCESS_SYNC_FAILED = (
    '模型调用授权同步失败，配置已保留，请重试发布；持续失败请联系管理员检查运行时权限，无需填写模型别名'
)
DEPLOYMENT_MISSING = '模型部署尚未就绪或已被删除，请检查接入配置后重试，无需填写模型别名'
ACCESS_SYNC_TIMEOUT_SECONDS = 20


class RuntimeModelAccessService:
    """发布时按具体部署增补内部调用权限，不改变平台用户的模型授权。"""

    def __init__(
        self,
        client: LiteLLMManagementClient | None,
        *,
        access_lock: Callable[[], AbstractAsyncContextManager[None]],
    ) -> None:
        self._client = client
        self._access_lock = access_lock

    async def ensure_model_access(self, alias: str) -> None:
        if not alias.strip() or '*' in alias or alias in {'all-proxy-models', 'all-team-models'}:
            raise ServiceException(message=DEPLOYMENT_MISSING)
        if self._client is None:
            raise ServiceException(message=ACCESS_SYNC_FAILED)
        failed = False
        cancelled = False
        operation = asyncio.create_task(self._sync(alias))
        try:
            await asyncio.wait_for(asyncio.shield(operation), timeout=ACCESS_SYNC_TIMEOUT_SECONDS)
        except (asyncio.CancelledError, asyncio.TimeoutError) as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            failed = not cancelled
            operation.cancel()
            try:
                await _finish_started_operation(operation)
            except (asyncio.CancelledError, Exception):
                # 先完成已发送的写请求及事务锁清理，再报告取消或超时。
                pass
        except (LiteLLMManagementError, SQLAlchemyError):
            failed = True
        if cancelled:
            raise asyncio.CancelledError
        if failed:
            raise ServiceException(message=ACCESS_SYNC_FAILED)

    async def _sync(self, alias: str) -> None:
        async with self._access_lock():
            if not await self._client.model_deployment_exists(alias):
                raise ServiceException(message=DEPLOYMENT_MISSING)
            models = await self._client.get_runtime_key_models()
            # 空列表及特殊值已经允许全部模型，不得收窄现有权限。
            if not models or alias in models or {'*', 'all-proxy-models'}.intersection(models):
                return
            await _finish_started_operation(
                asyncio.create_task(self._client.update_runtime_key_models([*models, alias]))
            )


async def _finish_started_operation(operation: asyncio.Task[None]) -> None:
    """HTTP 写入一旦发出，连续取消也必须等待其结束后才能释放序列化锁。"""
    cancelled = False
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:  # noqa: PERF203 - 必须接住每一次取消，直到已发送写入结束。
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not operation.cancelled():
            operation.exception()
        raise asyncio.CancelledError
    operation.result()
