import assert from 'node:assert/strict'
import * as models from '../../plugins/ai/utils/modelEngine.js'

assert.equal(typeof models.buildModelVisionUpdate, 'function')
const model = { modelType: 'chat', capabilities: {
  vision: false, streaming: true, reasoning: true, tools: true,
  dimensionsOverride: false, returnDocuments: false,
} }
const before = structuredClone(model)
assert.deepEqual(models.buildModelVisionUpdate(model, true), {
  capabilities: { streaming: true, vision: true, reasoning: true, tools: true },
})
assert.deepEqual(model, before, 'editing vision must not mutate the fetched model')
assert.deepEqual(models.buildModelVisionUpdate({ modelType: 'chat', capabilities: { vision: true } }, false), {
  capabilities: { streaming: false, vision: false, reasoning: false, tools: false },
})
for (const modelType of ['embedding', 'rerank', undefined]) {
  assert.throws(() => models.buildModelVisionUpdate({ modelType }, true), /对话模型/)
}
assert.throws(() => models.buildModelVisionUpdate(model, 'true'), /布尔/)
