class LiteLLMManagementError(Exception):
    """LiteLLM 管理面调用的稳定内部异常。"""

    status_code = 502
    code = 'LITELLM_MANAGEMENT_ERROR'
    default_message = 'LiteLLM 管理面调用失败'

    def __init__(self) -> None:
        self.message = self.default_message
        super().__init__(self.message)


class LiteLLMManagementMisconfiguredError(LiteLLMManagementError):
    """LiteLLM Management Key 尚未配置。"""

    status_code = 503
    code = 'LITELLM_MANAGEMENT_MISCONFIGURED'
    default_message = 'LiteLLM 管理面尚未完成配置'


class LiteLLMManagementUnavailableError(LiteLLMManagementError):
    """LiteLLM 管理面当前不可达。"""

    status_code = 503
    code = 'LITELLM_MANAGEMENT_UNAVAILABLE'
    default_message = 'LiteLLM 管理面当前不可用'


class LiteLLMManagementTimeoutError(LiteLLMManagementError):
    """LiteLLM 管理面请求超时。"""

    status_code = 504
    code = 'LITELLM_MANAGEMENT_TIMEOUT'
    default_message = 'LiteLLM 管理面请求超时'


class LiteLLMManagementAuthenticationError(LiteLLMManagementError):
    """Platform 内部 Management Key 被拒绝。"""

    code = 'LITELLM_MANAGEMENT_AUTHENTICATION_FAILED'
    default_message = 'LiteLLM 管理面内部认证失败'


class LiteLLMManagementUpstreamError(LiteLLMManagementError):
    """LiteLLM 管理面返回了无效或失败响应。"""

    code = 'LITELLM_MANAGEMENT_UPSTREAM_ERROR'
    default_message = 'LiteLLM 管理面上游调用失败'
