from collections.abc import Awaitable, Callable
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, NoReturn, TypeVar


class _ModelErrorKind(Enum):
    NOT_FOUND = (404, 'MODEL_NOT_FOUND', '平台模型不存在')
    DISABLED = (409, 'MODEL_DISABLED', '平台模型未发布或已停用')
    BINDING_MISSING = (503, 'MODEL_BINDING_MISSING', '平台模型缺少有效的运行时绑定')
    RUNTIME_MISCONFIGURED = (503, 'MODEL_RUNTIME_MISCONFIGURED', '模型运行时尚未完成配置')
    RUNTIME_UNAVAILABLE = (503, 'MODEL_RUNTIME_UNAVAILABLE', '模型运行时当前不可用')
    RUNTIME_TIMEOUT = (504, 'MODEL_RUNTIME_TIMEOUT', '模型运行时请求超时')
    RUNTIME_AUTH_FAILED = (502, 'MODEL_RUNTIME_AUTH_FAILED', '模型运行时内部认证失败')
    PROVIDER_AUTH_FAILED = (502, 'MODEL_PROVIDER_AUTH_FAILED', '模型服务商认证失败')
    PROVIDER_RATE_LIMITED = (502, 'MODEL_PROVIDER_RATE_LIMITED', '模型服务商请求受限')
    RUNTIME_ERROR = (502, 'MODEL_RUNTIME_ERROR', '模型运行时调用失败')


_ERROR_VIEW = MappingProxyType(
    {
        kind.name.lower(): kind.value
        for kind in _ModelErrorKind
    }
)
_KIND_BY_CODE = MappingProxyType({kind.value[1]: kind for kind in _ModelErrorKind})


def _restore_model_service_error(kind: _ModelErrorKind) -> 'ModelServiceError':
    return ModelServiceError(kind)


class ModelServiceError(Exception):
    """Model Service 对外稳定且脱敏的领域错误。"""

    _ERRORS: ClassVar = _ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('ModelServiceError 不允许继承')

    def __init__(self, kind: _ModelErrorKind) -> None:
        if type(kind) is not _ModelErrorKind:
            raise TypeError('ModelServiceError 只能通过固定工厂创建')
        status_code, code, public_message = kind.value
        self.status_code = status_code
        self.code = code
        self.public_message = public_message
        super().__init__(public_message)

    def __reduce__(self) -> tuple[Callable[[_ModelErrorKind], 'ModelServiceError'], tuple[_ModelErrorKind]]:
        return _restore_model_service_error, (_KIND_BY_CODE[self.code],)

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.public_message}

    @classmethod
    def not_found(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.NOT_FOUND)

    @classmethod
    def disabled(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.DISABLED)

    @classmethod
    def binding_missing(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.BINDING_MISSING)

    @classmethod
    def runtime_misconfigured(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.RUNTIME_MISCONFIGURED)

    @classmethod
    def runtime_unavailable(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.RUNTIME_UNAVAILABLE)

    @classmethod
    def runtime_timeout(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.RUNTIME_TIMEOUT)

    @classmethod
    def runtime_auth_failed(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.RUNTIME_AUTH_FAILED)

    @classmethod
    def provider_auth_failed(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.PROVIDER_AUTH_FAILED)

    @classmethod
    def provider_rate_limited(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.PROVIDER_RATE_LIMITED)

    @classmethod
    def runtime_error(cls) -> 'ModelServiceError':
        return ModelServiceError(_ModelErrorKind.RUNTIME_ERROR)


ResultT = TypeVar('ResultT')


async def _operation_failed(operation: Callable[[], Awaitable[ResultT]]) -> tuple[bool, ResultT | None]:
    try:
        return False, await operation()
    except Exception:
        return True, None


async def call_with_model_service_error(
    operation: Callable[[], Awaitable[ResultT]],
    error_factory: Callable[[], ModelServiceError],
) -> ResultT:
    """执行上游调用并在异常上下文外抛出脱敏领域错误。"""
    failed, result = await _operation_failed(operation)
    if failed:
        error = error_factory()
        if type(error) is not ModelServiceError:
            raise TypeError('错误工厂必须返回固定的 ModelServiceError')
        raise error
    return result  # type: ignore[return-value]
