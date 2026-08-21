import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'

import feedback, { registerFeedbackApis } from '../../src/plugins/antdFeedback.js'

const infrastructureFiles = [
  'src/plugins/modal.js',
  'src/plugins/download.js',
  'src/utils/request.js',
  'src/permission.js',
  'src/store/modules/user.js',
]

for (const fileName of infrastructureFiles) {
  const source = readFileSync(fileName, 'utf8')
  assert.doesNotMatch(source, /element-plus|ElMessage|ElNotification|ElMessageBox|ElLoading/)
}

assert.equal(existsSync('src/plugins/antdFeedback.js'), true)
assert.equal(existsSync('src/design/AntdFeedbackHost.vue'), true)

const feedbackSource = readFileSync('src/plugins/antdFeedback.js', 'utf8')
const feedbackHost = readFileSync('src/design/AntdFeedbackHost.vue', 'utf8')
const antdRoot = readFileSync('src/design/AntdRoot.vue', 'utf8')

for (const method of [
  'msg',
  'msgError',
  'msgSuccess',
  'msgWarning',
  'alert',
  'alertError',
  'alertSuccess',
  'alertWarning',
  'notify',
  'notifyError',
  'notifySuccess',
  'notifyWarning',
  'confirm',
  'prompt',
  'loading',
  'closeLoading',
]) {
  assert.match(feedbackSource, new RegExp(`\\b${method}\\b`))
}

assert.match(feedbackSource, /registerFeedbackApis/)
assert.match(feedbackHost, /App\.useApp\(\)/)
assert.match(feedbackHost, /registerFeedbackApis/)
assert.match(antdRoot, /a-app/)
assert.match(antdRoot, /AntdFeedbackHost/)

const calls = []
let confirmZIndex
const modal = {
  info(options) {
    calls.push(['alert', options.content])
    options.onOk()
  },
  confirm(options) {
    calls.push(['confirm', options.content])
    confirmZIndex = options.zIndex
    options.onOk()
  },
}
const message = {
  success(options) {
    calls.push(['message', options.content])
  },
}
const notification = {
  error(options) {
    calls.push(['notification', options.description])
  },
}
const unregister = registerFeedbackApis({ message, modal, notification })

feedback.msgSuccess('保存成功')
feedback.notifyError('请求失败')
await feedback.alert('已完成')
await feedback.confirm('继续操作')
const promptResult = await feedback.prompt('名称')
feedback.loading('Node 环境不创建 DOM')
feedback.closeLoading()
unregister()

assert.deepEqual(calls.map(([type]) => type), [
  'message',
  'notification',
  'alert',
  'confirm',
  'confirm',
])
assert.deepEqual(promptResult, { value: '' })
assert.ok(confirmZIndex > 1000)
