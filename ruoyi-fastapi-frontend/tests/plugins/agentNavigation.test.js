import assert from 'node:assert/strict'
import { resolveAgentExtensionGroups } from '../../src/components/Workspace/workspaceTransforms.js'

assert.deepEqual(resolveAgentExtensionGroups(new Set(['agent_extension:agent:list'])), [{
  key: 'agent-extension', label: '智能体扩展', path: '/extensions/agent', children: [
    { key: 'agent', label: '智能体管理', path: '/extensions/agent', permission: 'agent_extension:agent:list' },
  ],
}])
assert.deepEqual(resolveAgentExtensionGroups(new Set(['ai:knowledge:list'])), [{
  key: 'agent-extension', label: '智能体扩展', path: '/ai/knowledge', children: [
    { key: 'knowledge', label: '知识库', path: '/ai/knowledge', permission: 'ai:knowledge:list' },
  ],
}])
assert.deepEqual(resolveAgentExtensionGroups(new Set()), [])
assert.deepEqual(resolveAgentExtensionGroups(new Set(['*:*:*']))[0].children.map(item => item.key), ['agent', 'skill', 'knowledge'])
