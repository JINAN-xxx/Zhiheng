"""Public execution errors and diagnostics without SQL parameters or exception text."""

import re
from traceback import extract_tb

from sqlalchemy.exc import DBAPIError

from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError

CONCURRENCY_SQLSTATES = frozenset({'40P01', '40001', '55P03'})


def database_sqlstate(error: Exception) -> str | None:
    if not isinstance(error, DBAPIError):
        return None
    value = getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None)
    return value if isinstance(value, str) and re.fullmatch(r'[0-9A-Z]{5}', value) else None


def execution_error(error: Exception, *, provider_started: bool) -> RunError:
    if isinstance(error, AppError):
        return RunError(
            code=error.code,
            message='执行未完成，请查看运行与用量记录' if provider_started else error.message,
        )
    if isinstance(error, DBAPIError):
        if database_sqlstate(error) in CONCURRENCY_SQLSTATES:
            return RunError(
                code='execution_conflict',
                message='执行过程中发生数据库冲突，请查看运行与用量记录'
                if provider_started
                else '执行准备发生数据库冲突，请重试',
            )
        return RunError(
            code='execution_unavailable',
            message='执行过程中发生数据库异常，请查看运行与用量记录'
            if provider_started
            else '执行服务暂时不可用，请稍后重试',
        )
    return RunError(
        code='inference_failed' if provider_started else 'execution_failed',
        message='模型调用未完成，请查看运行与用量记录' if provider_started else '执行准备失败，请查看运行记录',
    )


def exception_frames(error: Exception) -> tuple[str, ...]:
    # Exception repr/str and traceback locals can contain credentials or model inputs.
    return tuple(f'{frame.filename}:{frame.lineno}:{frame.name}' for frame in extract_tb(error.__traceback__)[-8:])
