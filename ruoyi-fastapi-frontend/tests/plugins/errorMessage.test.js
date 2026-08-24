import assert from 'node:assert/strict'

import { normalizeErrorMessage } from '../../src/utils/errorMessage.js'

assert.equal(
  normalizeErrorMessage({ code: 'UPSTREAM_INVALID', message: '上游返回了无效数据' }),
  '上游返回了无效数据',
)
assert.equal(
  normalizeErrorMessage({ msg: { code: 502, message: '模型服务暂不可用' } }),
  '模型服务暂不可用',
)
assert.equal(normalizeErrorMessage(new Error('请求失败')), '请求失败')
assert.equal(normalizeErrorMessage({ code: 502 }, '系统接口异常'), '系统接口异常')

console.log('ok structured error message')
