from collections.abc import Callable
from types import MappingProxyType
from typing import ClassVar, NoReturn

_FIXED_ERROR_CONTRACTS = (
    (404, 'APPLICATION_NOT_FOUND', '应用不存在'),
    (409, 'APPLICATION_DISABLED', '应用已停用'),
    (409, 'APPLICATION_CODE_CONFLICT', '应用编码已存在'),
    (404, 'API_KEY_NOT_FOUND', 'API Key 不存在'),
    (409, 'API_KEY_INACTIVE', 'API Key 已失效'),
    (409, 'MODEL_GRANT_INVALID', '模型授权无效'),
    (409, 'QUOTA_VERSION_CONFLICT', '配额已被其他操作修改'),
    (503, 'DEVELOPER_API_MISCONFIGURED', 'Developer API 尚未完成安全配置'),
    (409, 'APPLICATION_VERSION_CONFLICT', '应用已被其他操作修改'),
    (409, 'APPLICATION_NOT_DISABLED', '应用必须先停用'),
    (409, 'APPLICATION_ACTIVE', '应用已启用'),
)
_ERROR_NAMES = (
    'application_not_found',
    'application_disabled',
    'application_code_conflict',
    'api_key_not_found',
    'api_key_inactive',
    'model_grant_invalid',
    'quota_conflict',
    'configuration_error',
    'application_conflict',
    'application_not_disabled',
    'application_active',
)
_ERROR_VIEW = MappingProxyType(dict(zip(_ERROR_NAMES, _FIXED_ERROR_CONTRACTS, strict=True)))
_CONSTRUCTION_TOKEN = object()


def _contract_for_index(
    kind_index: object,
    contracts: tuple[tuple[int, str, str], ...] = _FIXED_ERROR_CONTRACTS,
) -> tuple[int, str, str]:
    if type(kind_index) is not int or not 0 <= kind_index < len(contracts):
        raise TypeError('无效的Developer API错误分类')
    return contracts[kind_index]


def _trusted_index(error: 'DeveloperApiError') -> int:
    try:
        kind_index = object.__getattribute__(error, '_kind_index')
    except AttributeError:
        raise TypeError('无效的Developer API错误实例') from None
    _contract_for_index(kind_index)
    return kind_index


def _restore_developer_api_error(kind_index: int) -> 'DeveloperApiError':
    return _new_developer_api_error(kind_index)


def _new_developer_api_error(
    kind_index: int,
    token: object = _CONSTRUCTION_TOKEN,
) -> 'DeveloperApiError':
    return DeveloperApiError(kind_index, token)


class DeveloperApiError(Exception):
    """Developer API管理面对外稳定且脱敏的领域错误。"""

    __slots__ = ('_kind_index',)
    _ERRORS: ClassVar = _ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('DeveloperApiError 不允许继承')

    def __init__(self, kind_index: int, token: object | None = None) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError('DeveloperApiError 只能通过固定工厂创建')
        _contract_for_index(kind_index)
        object.__setattr__(self, '_kind_index', kind_index)
        object.__setattr__(self, 'args', ())

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError('DeveloperApiError 实例不可修改')

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError('DeveloperApiError 实例不可修改')

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
        return f'DeveloperApiError(code={self.code!r}, message={self.public_message!r})'

    def __reduce__(self) -> tuple[Callable[[int], 'DeveloperApiError'], tuple[int]]:
        return _restore_developer_api_error, (_trusted_index(self),)

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.public_message}

    @staticmethod
    def application_not_found() -> 'DeveloperApiError':
        return _new_developer_api_error(0)

    @staticmethod
    def application_disabled() -> 'DeveloperApiError':
        return _new_developer_api_error(1)

    @staticmethod
    def application_code_conflict() -> 'DeveloperApiError':
        return _new_developer_api_error(2)

    @staticmethod
    def api_key_not_found() -> 'DeveloperApiError':
        return _new_developer_api_error(3)

    @staticmethod
    def api_key_inactive() -> 'DeveloperApiError':
        return _new_developer_api_error(4)

    @staticmethod
    def model_grant_invalid() -> 'DeveloperApiError':
        return _new_developer_api_error(5)

    @staticmethod
    def quota_conflict() -> 'DeveloperApiError':
        return _new_developer_api_error(6)

    @staticmethod
    def configuration_error() -> 'DeveloperApiError':
        return _new_developer_api_error(7)

    @staticmethod
    def application_conflict() -> 'DeveloperApiError':
        return _new_developer_api_error(8)

    @staticmethod
    def application_not_disabled() -> 'DeveloperApiError':
        return _new_developer_api_error(9)

    @staticmethod
    def application_active() -> 'DeveloperApiError':
        return _new_developer_api_error(10)
