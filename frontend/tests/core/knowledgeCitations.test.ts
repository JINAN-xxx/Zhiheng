import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  formatKnowledgeCitations,
  groupKnowledgeSources
} from '../../src/modules/knowledge/citations.ts'
import type { KnowledgeHit } from '../../src/modules/knowledge/api/index.ts'

const documentId = 'bcbf48c3-6102-44e7-af5c-badf0b8574d0'
const fileId = '9d753e7a-36ff-4e46-b1e6-aefd0dfb2ec9'
const secondId = 'a4fe7421-bc97-42bd-b9e2-72509b92a840'
function hit(changes: Partial<KnowledgeHit> = {}): KnowledgeHit {
  return {
    knowledge_base_id: 'base-1',
    document_id: documentId,
    source_id: documentId,
    file_id: fileId,
    chunk_id: 'chunk-1',
    index_revision: 'revision-1',
    title: '接口说明.pdf',
    content: '真实片段',
    score: 0.8,
    ...changes
  }
}
const first = hit()
const second = hit({
  document_id: secondId,
  source_id: secondId,
  file_id: 'file-2',
  chunk_id: 'chunk-2'
})

void test('document numbers follow first retrieval rank and group interleaved fragments', () => {
  const later = hit({ chunk_id: 'chunk-3' })
  const sources = [first, second, later, first]
  const original = structuredClone(sources)
  assert.deepEqual(
    groupKnowledgeSources(sources).map((value) => [value.number, value.hits.length]),
    [
      [1, 2],
      [2, 1]
    ]
  )
  assert.deepEqual(sources, original)
})

void test('historical UUID references and new numeric references map to the same document', () => {
  assert.equal(
    formatKnowledgeCitations(`说明[${documentId}]，其他[${secondId}]，再次[1]。`, [first, second]),
    '说明[1]，其他[2]，再次[1]。'
  )
  assert.equal(formatKnowledgeCitations(`[${fileId}] [chunk-1]`, [first]), '[1] [1]')
})

void test('clickable citations use only the requested local target', () => {
  assert.equal(
    formatKnowledgeCitations(`[${documentId}] [2]`, [first, second], {
      linkPrefix: '#source-message-'
    }),
    '[\\[1\\]](#source-message-1 "查看来源 1：接口说明.pdf") [\\[2\\]](#source-message-2 "查看来源 2：接口说明.pdf")'
  )
})

void test('unknown identifiers and unrelated numeric brackets are preserved', () => {
  const text = `业务编号 ${documentId}，[unknown-source]，[99]，数组[0]。`
  assert.equal(formatKnowledgeCitations(text, [first]), text)
  assert.equal(formatKnowledgeCitations(`[${documentId}]`, []), `[${documentId}]`)
})

for (const code of [
  `\`[${documentId}]\``,
  `\`\`文字 \` [${documentId}]\`\``,
  `\`\`\`python\nvalues = [1]\nprint('[${documentId}]')\n\`\`\``,
  `~~~text\n[${documentId}]\n~~~`,
  `\`\`\`text\n[${documentId}]`,
  `\`[${documentId}]`
]) {
  void test(`code remains unchanged: ${JSON.stringify(code)}`, () => {
    assert.equal(
      formatKnowledgeCitations(`依据[${documentId}]\n${code}`, [first]),
      `依据[1]\n${code}`
    )
  })
}

void test('explicit links, images, reference links and definitions keep their semantics', () => {
  const text = `[1](https://example.org/) ![1](diagram.png) [1][ref]\n[1]: https://example.org/\n参见[1]和[${documentId}]`
  assert.equal(formatKnowledgeCitations(text, [first]), text.replace(`[${documentId}]`, '[1]'))
})

void test('escaped text is not converted into a citation', () => {
  const text = `\\[${documentId}]`
  assert.equal(formatKnowledgeCitations(text, [first]), text)
})

void test('partial known citations stay hidden during streaming and resolve when complete', () => {
  for (let length = 0; length < documentId.length; length++) {
    assert.equal(
      formatKnowledgeCitations(`依据[${documentId.slice(0, length)}`, [first], { loading: true }),
      '依据'
    )
  }
  assert.equal(
    formatKnowledgeCitations(`依据[${documentId}]`, [first], { loading: true }),
    '依据[1]'
  )
  assert.equal(
    formatKnowledgeCitations('依据[unrelated', [first], { loading: true }),
    '依据[unrelated'
  )
})

void test('an identifier shared by different documents is not assigned a false citation', () => {
  const shared = { ...second, file_id: first.file_id }
  assert.equal(formatKnowledgeCitations(`[${fileId}]`, [first, shared]), `[${fileId}]`)
})
