const failureMessages = Object.freeze({
  timeout: '连接测试超时，请检查服务地址和网络后重试',
  network: '无法连接模型服务，请检查服务地址、网络和证书配置',
  authentication: '模型服务拒绝了 API Key，请检查凭据是否正确或已失效',
  permission: '当前凭据没有访问该模型的权限，请检查服务商授权',
  quota: '模型账户余额或可用额度不足，请检查服务商账户',
  rate_limit: '模型服务请求频率受限，请稍后重试',
  model_not_found: '服务商未找到该模型，请核对完整模型名称',
  invalid_request: '模型服务不接受当前请求，请检查模型类型和参数',
  upstream_unavailable: '模型服务暂时不可用，请稍后重试',
  unknown: '连接测试失败，请检查模型类型、名称和连接配置',
})

const serviceMessages = Object.freeze({
  MODEL_ENGINE_CONNECTION_MISSING: '所选连接缺少可确定的服务地址，请新建完整连接',
  MODEL_ENGINE_CONNECTION_CONFLICT: '所选连接引用了多个服务地址，请新建明确指定地址的连接',
  MODEL_ENGINE_CONNECTION_INVALID: '所选连接的服务地址无效，请检查连接配置',
  MODEL_ENGINE_PROVIDER_MISMATCH: '所选连接不属于当前服务商，请重新选择连接',
  LITELLM_MANAGEMENT_TIMEOUT: '模型管理服务请求超时，请稍后重试',
  LITELLM_MANAGEMENT_UNAVAILABLE: '模型管理服务暂时无法连接，请稍后重试',
  LITELLM_MANAGEMENT_AUTHENTICATION_FAILED: '模型管理服务认证失败，请联系管理员检查平台配置',
  LITELLM_MANAGEMENT_MISCONFIGURED: '模型管理服务尚未配置，请联系管理员',
})

const transportMessages = Object.freeze({
  'LiteLLM 管理面请求超时': serviceMessages.LITELLM_MANAGEMENT_TIMEOUT,
  'LiteLLM 管理面当前不可用': serviceMessages.LITELLM_MANAGEMENT_UNAVAILABLE,
  'LiteLLM 管理面内部认证失败': serviceMessages.LITELLM_MANAGEMENT_AUTHENTICATION_FAILED,
  'LiteLLM 管理面尚未完成配置': serviceMessages.LITELLM_MANAGEMENT_MISCONFIGURED,
})

export function connectionFailureMessage(result, error) {
  if (Object.hasOwn(failureMessages, result?.failureReason)) return failureMessages[result.failureReason]
  const code = error?.response?.data?.detail?.code || error?.code
  if (Object.hasOwn(serviceMessages, code)) return serviceMessages[code]
  // 传输层有时只保留 Error.message；仅接受已知固定消息，不展示上游原文。
  if (Object.values(serviceMessages).includes(error?.message)) return error.message
  if (Object.hasOwn(transportMessages, error?.message)) return transportMessages[error.message]
  if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') return failureMessages.timeout
  if (error?.code === 'ERR_NETWORK') return failureMessages.network
  return failureMessages.unknown
}
