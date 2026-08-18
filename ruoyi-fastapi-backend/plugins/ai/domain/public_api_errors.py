from collections.abc import Callable
from types import MappingProxyType
from typing import ClassVar, NoReturn

_FIXED_ERROR_CONTRACTS = (
    (404, 'not_found', 'invalid_request_error', 'The requested resource was not found.'),
    (401, 'invalid_api_key', 'authentication_error', 'The API key is invalid or inactive.'),
    (403, 'permission_denied', 'permission_error', 'The API key cannot access this model.'),
    (404, 'model_not_found', 'invalid_request_error', 'The requested model was not found.'),
    (400, 'invalid_request', 'invalid_request_error', 'The request is invalid.'),
    (502, 'upstream_error', 'api_error', 'The model provider request failed.'),
    (503, 'service_unavailable', 'api_error', 'The model service is unavailable.'),
)
_ERROR_NAMES = (
    'feature_disabled',
    'invalid_api_key',
    'permission_denied',
    'model_not_found',
    'invalid_request',
    'upstream_error',
    'service_unavailable',
)
_ERROR_VIEW = MappingProxyType(dict(zip(_ERROR_NAMES, _FIXED_ERROR_CONTRACTS, strict=True)))
_CONSTRUCTION_TOKEN = object()


def _contract_for_index(
    kind_index: object,
    contracts: tuple[tuple[int, str, str, str], ...] = _FIXED_ERROR_CONTRACTS,
) -> tuple[int, str, str, str]:
    if type(kind_index) is not int or not 0 <= kind_index < len(contracts):
        raise TypeError('Invalid Public API error kind')
    return contracts[kind_index]


def _trusted_index(error: 'PublicApiError') -> int:
    try:
        kind_index = object.__getattribute__(error, '_kind_index')
    except AttributeError:
        raise TypeError('Invalid Public API error instance') from None
    _contract_for_index(kind_index)
    return kind_index


def _restore_public_api_error(kind_index: int) -> 'PublicApiError':
    return _new_public_api_error(kind_index)


def _new_public_api_error(kind_index: int, token: object = _CONSTRUCTION_TOKEN) -> 'PublicApiError':
    return PublicApiError(kind_index, token)


class PublicApiError(Exception):
    """Stable OpenAI-compatible error that never carries request or provider data."""

    __slots__ = ('_kind_index',)
    _ERRORS: ClassVar = _ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('PublicApiError cannot be subclassed')

    def __init__(self, kind_index: int, token: object | None = None) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError('PublicApiError must be created by a fixed factory')
        _contract_for_index(kind_index)
        object.__setattr__(self, '_kind_index', kind_index)
        object.__setattr__(self, 'args', ())

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError('PublicApiError is immutable')

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError('PublicApiError is immutable')

    @property
    def status_code(self) -> int:
        return _contract_for_index(_trusted_index(self))[0]

    @property
    def code(self) -> str:
        return _contract_for_index(_trusted_index(self))[1]

    @property
    def error_type(self) -> str:
        return _contract_for_index(_trusted_index(self))[2]

    @property
    def public_message(self) -> str:
        return _contract_for_index(_trusted_index(self))[3]

    def __str__(self) -> str:
        return self.public_message

    def __repr__(self) -> str:
        return f'PublicApiError(code={self.code!r}, message={self.public_message!r})'

    def __reduce__(self) -> tuple[Callable[[int], 'PublicApiError'], tuple[int]]:
        return _restore_public_api_error, (_trusted_index(self),)

    def public_dict(self) -> dict[str, dict[str, str | None]]:
        return {
            'error': {
                'message': self.public_message,
                'type': self.error_type,
                'param': None,
                'code': self.code,
            }
        }

    @staticmethod
    def feature_disabled() -> 'PublicApiError':
        return _new_public_api_error(0)

    @staticmethod
    def invalid_api_key() -> 'PublicApiError':
        return _new_public_api_error(1)

    @staticmethod
    def permission_denied() -> 'PublicApiError':
        return _new_public_api_error(2)

    @staticmethod
    def model_not_found() -> 'PublicApiError':
        return _new_public_api_error(3)

    @staticmethod
    def invalid_request() -> 'PublicApiError':
        return _new_public_api_error(4)

    @staticmethod
    def upstream_error() -> 'PublicApiError':
        return _new_public_api_error(5)

    @staticmethod
    def service_unavailable() -> 'PublicApiError':
        return _new_public_api_error(6)
