class LiteLLMError(Exception):
    """LiteLLM Runtime 调用的稳定内部异常。"""

    status_code = 502
    code = 'LITELLM_ERROR'
    default_message = 'LiteLLM 服务调用失败'

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class LiteLLMMisconfiguredError(LiteLLMError):
    """LiteLLM Runtime 配置缺失。"""

    status_code = 503
    code = 'LITELLM_MISCONFIGURED'
    default_message = 'LiteLLM Runtime 尚未完成配置'


class LiteLLMUnavailableError(LiteLLMError):
    """LiteLLM Proxy 当前不可达。"""

    status_code = 503
    code = 'LITELLM_UNAVAILABLE'
    default_message = 'LiteLLM Runtime 当前不可用'


class LiteLLMTimeoutError(LiteLLMError):
    """LiteLLM Proxy 请求超时。"""

    status_code = 504
    code = 'LITELLM_TIMEOUT'
    default_message = 'LiteLLM Runtime 请求超时'


class LiteLLMAuthenticationError(LiteLLMError):
    """Platform 内部 Runtime Key 被拒绝。"""

    status_code = 502
    code = 'LITELLM_AUTHENTICATION_FAILED'
    default_message = 'LiteLLM Runtime 内部认证失败'


class LiteLLMProviderAuthenticationError(LiteLLMError):
    """LiteLLM 上游 Provider Credential 被拒绝。"""

    status_code = 502
    code = 'LITELLM_PROVIDER_AUTHENTICATION_FAILED'
    default_message = 'LiteLLM Provider 认证失败'


class LiteLLMProviderRateLimitError(LiteLLMError):
    """LiteLLM 上游 Provider 触发限流。"""

    status_code = 502
    code = 'LITELLM_PROVIDER_RATE_LIMITED'
    default_message = 'LiteLLM Provider 请求受限'


class LiteLLMUpstreamError(LiteLLMError):
    """LiteLLM 或其上游 Provider 返回错误。"""

    status_code = 502
    code = 'LITELLM_UPSTREAM_ERROR'
    default_message = 'LiteLLM Runtime 上游调用失败'
