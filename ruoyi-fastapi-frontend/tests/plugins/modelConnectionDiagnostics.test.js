import assert from 'node:assert/strict'
import { connectionFailureMessage } from '../../plugins/ai/utils/connectionDiagnostics.js'

for (const [reason, part] of [['timeout', '超时'], ['network', '网络'], ['authentication', 'API Key'], ['permission', '权限'], ['quota', '余额'], ['rate_limit', '频率'], ['model_not_found', '模型名称'], ['invalid_request', '模型类型'], ['upstream_unavailable', '暂时不可用']]) {
  const message = connectionFailureMessage({ failureReason: reason, message: 'api-key-secret https://private-host' })
  assert.match(message, new RegExp(part))
  assert.doesNotMatch(message, /secret|private-host/)
}
assert.doesNotMatch(connectionFailureMessage({ message: 'server-secret' }, new Error('request-secret')), /secret/)
assert.match(connectionFailureMessage(null, { response: { data: { detail: { code: 'MODEL_ENGINE_PROVIDER_MISMATCH', message: 'secret' } } } }), /服务商/)
assert.match(connectionFailureMessage(null, { response: { data: { detail: { code: 'MODEL_ENGINE_CONNECTION_MISSING' } } } }), /地址/)
assert.match(connectionFailureMessage(null, new Error('LiteLLM 管理面请求超时')), /模型管理服务请求超时/)
assert.match(connectionFailureMessage(null, new Error('LiteLLM 管理面当前不可用')), /模型管理服务暂时无法连接/)
assert.match(connectionFailureMessage(null, new Error('LiteLLM 管理面内部认证失败')), /模型管理服务认证失败/)
