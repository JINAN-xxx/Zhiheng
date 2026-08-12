from typing import ClassVar


class ModelServiceError(Exception):
    """Model Service 对外稳定且脱敏的领域错误。"""

    _ERRORS: ClassVar[dict[str, tuple[int, str, str]]] = {
        'not_found': (404, 'MODEL_NOT_FOUND', '平台模型不存在'),
        'disabled': (409, 'MODEL_DISABLED', '平台模型未发布或已停用'),
        'binding_missing': (503, 'MODEL_BINDING_MISSING', '平台模型缺少有效的运行时绑定'),
        'runtime_misconfigured': (503, 'MODEL_RUNTIME_MISCONFIGURED', '模型运行时尚未完成配置'),
        'runtime_unavailable': (503, 'MODEL_RUNTIME_UNAVAILABLE', '模型运行时当前不可用'),
        'runtime_timeout': (504, 'MODEL_RUNTIME_TIMEOUT', '模型运行时请求超时'),
        'runtime_auth_failed': (502, 'MODEL_RUNTIME_AUTH_FAILED', '模型运行时内部认证失败'),
        'provider_auth_failed': (502, 'MODEL_PROVIDER_AUTH_FAILED', '模型服务商认证失败'),
        'provider_rate_limited': (502, 'MODEL_PROVIDER_RATE_LIMITED', '模型服务商请求受限'),
        'runtime_error': (502, 'MODEL_RUNTIME_ERROR', '模型运行时调用失败'),
    }

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError('ModelServiceError 只能通过固定工厂创建')

    @classmethod
    def _create(cls, name: str) -> 'ModelServiceError':
        error_contract = cls._ERRORS.get(name)
        if error_contract is None:
            raise ValueError('未知的模型服务错误类型') from None
        status_code, code, public_message = error_contract
        error = Exception.__new__(cls)
        error.status_code = status_code
        error.code = code
        error.public_message = public_message
        Exception.__init__(error, public_message)
        return error

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.public_message}

    @classmethod
    def not_found(cls) -> 'ModelServiceError':
        return cls._create('not_found')

    @classmethod
    def disabled(cls) -> 'ModelServiceError':
        return cls._create('disabled')

    @classmethod
    def binding_missing(cls) -> 'ModelServiceError':
        return cls._create('binding_missing')

    @classmethod
    def runtime_misconfigured(cls) -> 'ModelServiceError':
        return cls._create('runtime_misconfigured')

    @classmethod
    def runtime_unavailable(cls) -> 'ModelServiceError':
        return cls._create('runtime_unavailable')

    @classmethod
    def runtime_timeout(cls) -> 'ModelServiceError':
        return cls._create('runtime_timeout')

    @classmethod
    def runtime_auth_failed(cls) -> 'ModelServiceError':
        return cls._create('runtime_auth_failed')

    @classmethod
    def provider_auth_failed(cls) -> 'ModelServiceError':
        return cls._create('provider_auth_failed')

    @classmethod
    def provider_rate_limited(cls) -> 'ModelServiceError':
        return cls._create('provider_rate_limited')

    @classmethod
    def runtime_error(cls) -> 'ModelServiceError':
        return cls._create('runtime_error')
