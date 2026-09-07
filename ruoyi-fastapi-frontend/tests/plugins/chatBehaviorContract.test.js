import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { resolveChatModelOptions } from '../../plugins/ai/utils/chatTransforms.js'

const models = [
  { modelId: 'chat-1', modelCode: 'gpt-chat', title: '聊天模型', modelType: 'chat' },
  { modelId: 'embed-1', modelCode: 'text-embed', title: '向量模型', modelType: 'embedding' },
  { modelId: 'rank-1', modelCode: 'bge-rerank', title: '重排模型', modelType: 'rerank' },
  { modelId: 'chat-2', modelCode: 'fallback-chat', modelType: 'chat' },
  { modelId: 'unknown', modelCode: 'unknown', title: '缺失类型' },
]

assert.deepEqual(resolveChatModelOptions(models), [
  { label: '聊天模型', title: '聊天模型', modelCode: 'gpt-chat', value: 'chat-1' },
  { label: 'fallback-chat', title: 'fallback-chat', modelCode: 'fallback-chat', value: 'chat-2' },
])
assert.deepEqual(resolveChatModelOptions([]), [])
assert.deepEqual(resolveChatModelOptions(), [])

const chatView = readFileSync('plugins/ai/views/chat/index.vue', 'utf8')
const skillPicker = readFileSync('plugins/ai/views/chat/components/SkillPicker.vue', 'utf8')
assert.match(chatView, /resolveChatModelOptions/)
assert.match(chatView, /modelOptions\s*=\s*computed\(\(\)\s*=>\s*resolveChatModelOptions\(models\.value\)\)/)
assert.match(chatView, /chatModels\.some\(\(model\)\s*=>\s*model\.modelId\s*===\s*modelId\.value\)/)
assert.match(chatView, /modelId\.value\s*=\s*chatModels\[0\]\?\.modelId/)
assert.match(chatView, /normalizeErrorMessage/)
assert.match(chatView, /normalizeErrorMessage\(event\.data, ['"]模型运行失败['"]\)/)
assert.match(chatView, /<SkillPicker/)
assert.match(chatView, /skillSnapshots/)
assert.match(chatView, /skillSlugs:\s*outgoingSkills\.map/)
assert.match(chatView, /selectedSkills\.value\s*=\s*\[\]/)
assert.match(chatView, /shouldOpenSkillPicker/)
assert.match(skillPicker, /listAvailableSkills/)
assert.match(skillPicker, /最多选择 5 个技能/)
assert.match(skillPicker, /选择技能/)

const sendBody = chatView.slice(chatView.indexOf('async function send()'))
const clearDraftIndex = sendBody.search(/draft\.value\s*=\s*["']{2}/)
const clearImagesIndex = sendBody.search(/imageIds\.value\s*=\s*\[\]/)
const clearSkillsIndex = sendBody.search(/selectedSkills\.value\s*=\s*\[\]/)
const nextTickIndex = sendBody.indexOf('await nextTick()')
const requestIndex = sendBody.indexOf('createChatStream(')
assert.ok(clearDraftIndex >= 0)
assert.ok(clearImagesIndex > clearDraftIndex)
assert.ok(nextTickIndex > clearImagesIndex)
assert.ok(clearSkillsIndex > clearImagesIndex)
assert.ok(nextTickIndex > clearSkillsIndex)
assert.ok(requestIndex > nextTickIndex)

console.log('ok chat behavior contract')
