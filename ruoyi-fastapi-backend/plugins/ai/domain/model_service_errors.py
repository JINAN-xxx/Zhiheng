from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import ClassVar, NoReturn, TypeVar

_FIXED_ERROR_CONTRACTS = (
    (404, 'MODEL_NOT_FOUND', '平台模型不存在'),
    (409, 'MODEL_DISABLED', '平台模型未发布或已停用'),
    (503, 'MODEL_BINDING_MISSING', '平台模型缺少有效的运行时绑定'),
    (503, 'MODEL_RUNTIME_MISCONFIGURED', '模型运行时尚未完成配置'),
    (503, 'MODEL_RUNTIME_UNAVAILABLE', '模型运行时当前不可用'),
    (504, 'MODEL_RUNTIME_TIMEOUT', '模型运行时请求超时'),
    (502, 'MODEL_RUNTIME_AUTH_FAILED', '模型运行时内部认证失败'),
    (502, 'MODEL_PROVIDER_AUTH_FAILED', '模型服务商认证失败'),
    (502, 'MODEL_PROVIDER_RATE_LIMITED', '模型服务商请求受限'),
    (502, 'MODEL_RUNTIME_ERROR', '模型运行时调用失败'),
)
_ERROR_NAMES = (
    'not_found',
    'disabled',
    'binding_missing',
    'runtime_misconfigured',
    'runtime_unavailable',
    'runtime_timeout',
    'runtime_auth_failed',
    'provider_auth_failed',
    'provider_rate_limited',
    'runtime_error',
)
_ERROR_VIEW = MappingProxyType(dict(zip(_ERROR_NAMES, _FIXED_ERROR_CONTRACTS, strict=True)))
_CONSTRUCTION_TOKEN = object()


def _contract_for_index(
    kind_index: object,
    contracts: tuple[tuple[int, str, str], ...] = _FIXED_ERROR_CONTRACTS,
) -> tuple[int, str, str]:
    if type(kind_index) is not int or not 0 <= kind_index < len(contracts):
        raise TypeError('无效的模型服务错误分类')
    return contracts[kind_index]


def _trusted_index(error: 'ModelServiceError') -> int:
    try:
        kind_index = object.__getattribute__(error, '_kind_index')
    except AttributeError:
        raise TypeError('无效的模型服务错误实例') from None
    _contract_for_index(kind_index)
    return kind_index


def _restore_model_service_error(kind_index: int) -> 'ModelServiceError':
    return _new_model_service_error(kind_index)


def _new_model_service_error(
    kind_index: int,
    token: object = _CONSTRUCTION_TOKEN,
) -> 'ModelServiceError':
    return ModelServiceError(kind_index, token)


class ModelServiceError(Exception):
    """Model Service 对外稳定且脱敏的领域错误。"""

    __slots__ = ('_kind_index',)
    _ERRORS: ClassVar = _ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('ModelServiceError 不允许继承')

    def __init__(self, kind_index: int, token: object | None = None) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError('ModelServiceError 只能通过固定工厂创建')
        _contract_for_index(kind_index)
        object.__setattr__(self, '_kind_index', kind_index)
        object.__setattr__(self, 'args', ())

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError('ModelServiceError 实例不可修改')

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError('ModelServiceError 实例不可修改')

    @property
    def status_code(self) -> int:
        return _contract_for_index(_trusted_index(self))[0]

    @property
    def code(self) -> str:
        return _contract_for_index(_trusted_index(self))[1]

    @property
    def public_message(self) -> str:
        return _contract_for_index(_trusted_index(self))[2]

    def __str__(self) -> str:
        return self.public_message

    def __repr__(self) -> str:
        return f'ModelServiceError(code={self.code!r}, message={self.public_message!r})'

    def __reduce__(self) -> tuple[Callable[[int], 'ModelServiceError'], tuple[int]]:
        return _restore_model_service_error, (_trusted_index(self),)

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.public_message}

    @staticmethod
    def not_found() -> 'ModelServiceError':
        return _new_model_service_error(0)

    @staticmethod
    def disabled() -> 'ModelServiceError':
        return _new_model_service_error(1)

    @staticmethod
    def binding_missing() -> 'ModelServiceError':
        return _new_model_service_error(2)

    @staticmethod
    def runtime_misconfigured() -> 'ModelServiceError':
        return _new_model_service_error(3)

    @staticmethod
    def runtime_unavailable() -> 'ModelServiceError':
        return _new_model_service_error(4)

    @staticmethod
    def runtime_timeout() -> 'ModelServiceError':
        return _new_model_service_error(5)

    @staticmethod
    def runtime_auth_failed() -> 'ModelServiceError':
        return _new_model_service_error(6)

    @staticmethod
    def provider_auth_failed() -> 'ModelServiceError':
        return _new_model_service_error(7)

    @staticmethod
    def provider_rate_limited() -> 'ModelServiceError':
        return _new_model_service_error(8)

    @staticmethod
    def runtime_error() -> 'ModelServiceError':
        return _new_model_service_error(9)


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
    """执行上游调用并在异常上下文外抛出规范化领域错误。"""
    failed, result = await _operation_failed(operation)
    if failed:
        candidate = error_factory()
        if type(candidate) is not ModelServiceError:
            raise TypeError('错误工厂必须返回固定的 ModelServiceError')
        raise _new_model_service_error(_trusted_index(candidate))
    return result  # type: ignore[return-value]
