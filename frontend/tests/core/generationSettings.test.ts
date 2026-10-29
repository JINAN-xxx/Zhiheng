import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextSendIntent } from '../../src/modules/chat/api/sendIntent.ts'
import { parseMessage } from '../../src/modules/chat/api/index.ts'

const input = { conversationId: 'c1', model_id: 'm1', message: 'hello' }
const settings = { system_prompt: '请简洁回答', temperature: 0.4, max_output_tokens: 128 }

void test('each generation parameter changes the logical send identity', () => {
  let ids = 0
  const createId = () => `settings-request-${++ids}`
  const first = nextSendIntent(null, { ...input, ...settings }, createId)
  for (const change of [
    { system_prompt: '请详细回答' },
    { temperature: 0 },
    { max_output_tokens: 64 }
  ]) {
    const changed = nextSendIntent(first, { ...input, ...settings, ...change }, createId)
    assert.notEqual(changed.client_request_id, first.client_request_id)
  }
  assert.equal(nextSendIntent(first, { ...input, ...settings }, createId), first)
})

void test('default settings are omitted and share the existing send identity', () => {
  const first = nextSendIntent(null, input, () => 'original-key')
  const explicitDefaults = nextSendIntent(
    first,
    { ...input, system_prompt: '', temperature: null, max_output_tokens: null },
    () => 'new-key'
  )
  assert.equal(explicitDefaults, first)
  const normalized = nextSendIntent(
    null,
    { ...input, system_prompt: '', temperature: null, max_output_tokens: null },
    () => 'default-key'
  )
  assert.equal('system_prompt' in normalized, false)
  assert.equal('temperature' in normalized, false)
  assert.equal('max_output_tokens' in normalized, false)
})

void test('generation settings remain frozen after the editor changes', () => {
  const mutable = { ...input, ...settings }
  const first = nextSendIntent(null, mutable, () => 'original-key')
  mutable.system_prompt = '改过的提示词'
  mutable.temperature = 2
  mutable.max_output_tokens = 256
  assert.deepEqual(
    {
      system_prompt: first.system_prompt,
      temperature: first.temperature,
      max_output_tokens: first.max_output_tokens
    },
    settings
  )
})

const rawMessage = {
  id: 'a1',
  role: 'assistant',
  content: '回答',
  run_id: null,
  created_at: '2026-10-29T00:00:00Z'
}
void test('message parser preserves an authorized generation snapshot for conversation restore', () => {
  const config = { model_id: 'm1', ...settings }
  const message = parseMessage({ ...rawMessage, generation_config: config })
  assert.deepEqual('generation_config' in message ? message.generation_config : undefined, config)
})
void test('legacy messages restore defaults without fabricating a generation snapshot', () => {
  const message = parseMessage(rawMessage)
  assert.equal('generation_config' in message ? message.generation_config : undefined, null)
})
void test('message parser rejects malformed generation snapshots', () => {
  for (const config of [
    { model_id: 'm1', ...settings, temperature: true },
    { model_id: 'm1', ...settings, temperature: Infinity },
    { model_id: 'm1', ...settings, max_output_tokens: 1.2 },
    { model_id: 'm1', ...settings, system_prompt: 'bad\0prompt' }
  ])
    assert.throws(() => parseMessage({ ...rawMessage, generation_config: config }))
})

void test('empty numeric edits use model defaults instead of retaining the last number', async () => {
  const generation = await import('../../src/modules/chat/generationSettings.ts')
  assert.equal(typeof generation.generationSettingsFromDraft, 'function')
  assert.deepEqual(
    generation.generationSettingsFromDraft({
      system_prompt: '  prompt\n',
      temperature: ' ',
      max_output_tokens: ''
    }),
    { system_prompt: '  prompt\n', temperature: null, max_output_tokens: null }
  )
  assert.deepEqual(
    generation.generationSettingsFromDraft({
      system_prompt: '',
      temperature: '0',
      max_output_tokens: '512'
    }),
    { system_prompt: '', temperature: 0, max_output_tokens: 512 }
  )
  for (const temperature of ['NaN', 'Infinity', '2.1', '-0.1', '错误']) {
    assert.match(
      generation.generationSettingsError(
        generation.generationSettingsFromDraft({
          system_prompt: '',
          temperature,
          max_output_tokens: ''
        }),
        512
      ),
      /温度须为/
    )
  }
  for (const max_output_tokens of ['NaN', 'Infinity', '0', '513', '1.5', '错误']) {
    assert.match(
      generation.generationSettingsError(
        generation.generationSettingsFromDraft({
          system_prompt: '',
          temperature: '',
          max_output_tokens
        }),
        512
      ),
      /输出长度须为/
    )
  }
})

void test('generation validation respects Unicode prompt length and the current model limit', async () => {
  const { generationSettingsError } = await import('../../src/modules/chat/generationSettings.ts')
  assert.equal(generationSettingsError({ ...settings, system_prompt: '😀'.repeat(32000) }, 512), '')
  assert.match(
    generationSettingsError({ ...settings, system_prompt: '😀'.repeat(32001) }, 512),
    /32000/
  )
  assert.match(generationSettingsError({ ...settings, system_prompt: '\ud800' }, 512), /无效字符/)
  assert.match(generationSettingsError({ ...settings, max_output_tokens: 513 }, 512), /1–512/)
})
