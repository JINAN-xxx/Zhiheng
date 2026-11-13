import assert from 'node:assert/strict'
import { resolve } from 'node:path'
import test from 'node:test'
import { Linter } from 'eslint'
import tseslint from 'typescript-eslint'
import { moduleBoundaries } from '../../tools/moduleBoundaries.js'

function lint(filename: string, source: string) {
  return new Linter().verify(
    source,
    [
      {
        files: ['**/*.ts'],
        languageOptions: { parser: tseslint.parser, ecmaVersion: 'latest', sourceType: 'module' },
        plugins: { architecture: { rules: { 'module-boundaries': moduleBoundaries } } },
        rules: { 'architecture/module-boundaries': 'error' }
      }
    ],
    { filename: resolve(filename) }
  )
}

void test('cross-domain private imports and reexports fail the actual lint rule', () => {
  for (const source of [
    "import { parsePoints } from '../billing/presentation.ts'",
    "export { parsePoints } from '../billing/api/index.ts'",
    "const component = import('../billing/components/CreditsPanel.vue')"
  ]) {
    const messages = lint('src/modules/chat/probe.ts', source)
    assert.equal(messages.length, 1)
    assert.equal(messages[0]?.ruleId, 'architecture/module-boundaries')
  }
})

void test('module public and route entries and same-domain internals are allowed', () => {
  for (const source of [
    "import { parsePoints } from '../billing/public.ts'",
    "export { chatRoutes } from './routes.ts'",
    "const view = import('./views/ChatView.vue')"
  ])
    assert.deepEqual(lint('src/modules/chat/probe.ts', source), [])
})

void test('shared cannot depend on app or business modules', () => {
  for (const source of [
    "import { identity } from '../modules/identity/public.ts'",
    "import { router } from '../app/router.ts'"
  ])
    assert.equal(lint('src/shared/probe.ts', source).length, 1)
})

void test('application composition consumes modules only through their public entries', () => {
  assert.equal(
    lint('src/app/probe.ts', "import { getModels } from '../modules/models/api/index.ts'").length,
    1
  )
  assert.deepEqual(
    lint('src/app/probe.ts', "import { getModels } from '../modules/models/public.ts'"),
    []
  )
})

void test('application shell cannot own business HTTP protocols', () => {
  const messages = lint(
    'src/app/components/probe.ts',
    "import { http as client } from '../../shared/http.ts'; client.request('/admin/notices/published')"
  )
  assert.equal(messages.length, 1)
  assert.equal(messages[0]?.ruleId, 'architecture/module-boundaries')
})

void test('inline TypeScript import types cannot bypass module public entries', () => {
  const messages = lint(
    'src/modules/chat/probe.ts',
    "type Value = import('../billing/api/types.ts').CreditRate"
  )
  assert.equal(messages.length, 1)
  assert.equal(messages[0]?.ruleId, 'architecture/module-boundaries')
})

void test('configured aliases and local root paths obey the same module ownership rules', () => {
  for (const path of [
    '@/modules/billing/presentation.ts',
    '~/src/modules/billing/presentation.ts',
    '/src/modules/billing/presentation.ts'
  ]) {
    assert.equal(lint('src/shared/probe.ts', `import { value } from '${path}'`).length, 1)
    assert.equal(lint('src/modules/chat/probe.ts', `type Value = import('${path}').Rate`).length, 1)
    assert.equal(lint('src/modules/chat/probe.ts', `const view = import('${path}')`).length, 1)
  }
  assert.deepEqual(
    lint('src/modules/chat/probe.ts', "import { parsePoints } from '@/modules/billing/public.ts'"),
    []
  )
})

void test('aliased HTTP imports do not let the application shell own protocols', () => {
  for (const path of ['@/shared/http.ts', '~/src/shared/http.ts', '@/shared/http']) {
    for (const method of ['request', 'open']) {
      const messages = lint(
        'src/app/components/probe.ts',
        `import { http as client } from '${path}'; client.${method}('/admin/notices/published')`
      )
      assert.equal(messages.length, 1)
      assert.equal(messages[0]?.ruleId, 'architecture/module-boundaries')
    }
  }
})
