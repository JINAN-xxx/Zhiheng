import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  artifactsByRun,
  artifactReference,
  formatArtifactLinks,
  previewKind
} from '../../src/modules/work/artifacts.ts'
import type { WorkArtifact } from '../../src/modules/work/api/index.ts'

function file(
  id: string,
  runId = 'run-1',
  version = 1,
  logicalPath = 'outputs/report.md'
): WorkArtifact {
  return {
    id,
    runId,
    version,
    logicalPath,
    fileId: `file-${id}`,
    name: logicalPath.split('/').at(-1)!,
    size: 20,
    mime: 'text/markdown',
    sha256: 'a'.repeat(64),
    validation: { valid: true },
    previewFileId: null,
    createdAt: '2026-11-20T00:00:00Z'
  }
}

void test('a later revision stays on its own reply after reloading the task', () => {
  const old = file('old'),
    revised = file('revised', 'run-2', 2),
    final = file('final', 'run-2', 3)
  const runs = artifactsByRun([final, old, revised])
  assert.deepEqual(runs.get('run-1'), [old])
  assert.deepEqual(runs.get('run-2'), [final])
})

void test('file references resolve registered paths and reject ambiguous or external references', () => {
  const old = file('old'),
    latest = file('latest', 'run-2', 2)
  const items = [old, latest]
  for (const href of [
    'outputs/report.md',
    'sandbox:/mnt/data/report.md',
    '/workspace/outputs/report.md',
    'report.md'
  ]) {
    assert.equal(artifactReference(href, items, 'task-1'), latest)
  }
  assert.equal(
    artifactReference('/api/work/tasks/task-1/artifacts/old/download', items, 'task-1'),
    old
  )
  for (const href of [
    'https://example.com/outputs/report.md',
    '//example.com/report.md',
    '../report.md',
    '%ZZ',
    '/api/work/tasks/task-2/artifacts/old/download'
  ]) {
    assert.equal(artifactReference(href, items, 'task-1'), undefined)
  }
  assert.equal(
    artifactReference(
      'report.md',
      [...items, file('other', 'run-2', 1, 'outputs/nested/report.md')],
      'task-1'
    ),
    undefined
  )
})

void test('registered Markdown file links open the preview without rewriting code or external links', () => {
  const content =
    '[报告](outputs/report.md)\n![报告图片](sandbox:/mnt/data/report.md)\n`[代码](outputs/report.md)`\n```md\n[示例](outputs/report.md)\n```\n[网站](https://example.com/report.md)'
  const result = formatArtifactLinks(content, [file('id-1')], 'task-1', '#preview-')
  assert.ok(result.startsWith('[报告](#preview-id-1 "预览 report.md")\n[报告图片](#preview-id-1'))
  assert.ok(result.includes('`[代码](outputs/report.md)`'))
  assert.ok(result.includes('```md\n[示例](outputs/report.md)\n```'))
  assert.ok(result.endsWith('[网站](https://example.com/report.md)'))
})

void test('preview treats executable formats as downloads and recognizes supported documents', () => {
  assert.equal(previewKind('image/svg+xml', 'image.svg'), '')
  assert.equal(previewKind('application/octet-stream', 'tool.py'), '')
  assert.equal(previewKind('application/pdf'), 'pdf')
  assert.equal(previewKind('text/plain', 'report.md'), 'markdown')
  assert.equal(previewKind('application/json', 'data.json'), 'text')
})

void test('bold file names become links while other emphasized text stays unchanged', () => {
  const result = formatArtifactLinks(
    '**report.md** 和 **说明**',
    [file('id-1')],
    'task-1',
    '#preview-'
  )
  assert.equal(result, '[**report.md**](#preview-id-1 "预览 report.md") 和 **说明**')
})
