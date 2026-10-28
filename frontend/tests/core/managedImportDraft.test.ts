import test from 'node:test'
import assert from 'node:assert/strict'
import {
  managedImportStorageKey,
  parseManagedImportDraft
} from '../../src/modules/knowledge/managedImportDraft.ts'

const draft = () => ({
  version: 1,
  userId: 'user-1',
  baseId: 'base-1',
  request: {
    file_ids: ['file-a', 'file-b'],
    folder_id: 'folder-1',
    chunk_size: 777,
    chunk_overlap: 111,
    client_request_id: 'original-request'
  },
  files: [
    { id: 'file-a', name: '原文甲.txt' },
    { id: 'file-b', name: '原文乙.md' }
  ]
})

void test('restoring an uncertain import retains the complete original request', () => {
  const original = draft()
  const restored = parseManagedImportDraft(JSON.parse(JSON.stringify(original)), 'user-1', 'base-1')
  assert.deepEqual(restored, original)
  restored.request.file_ids.push('local-change')
  assert.deepEqual(original.request.file_ids, ['file-a', 'file-b'])
})

void test('a saved import cannot be restored by another user or knowledge base', () => {
  assert.throws(() => parseManagedImportDraft(draft(), 'other-user', 'base-1'))
  assert.throws(() => parseManagedImportDraft(draft(), 'user-1', 'other-base'))
})

void test('malformed or incomplete saved imports cannot create a new request', () => {
  for (const value of [null, [], {}, { ...draft(), version: 2 }, { ...draft(), request: {} }]) {
    assert.throws(() => parseManagedImportDraft(value, 'user-1', 'base-1'))
  }
  for (const changes of [
    { file_ids: [] },
    { file_ids: ['file-a', 'file-a'] },
    { chunk_size: 0 },
    { chunk_overlap: 777 },
    { client_request_id: '' }
  ]) {
    const value = draft()
    assert.throws(() =>
      parseManagedImportDraft(
        { ...value, request: { ...value.request, ...changes } },
        'user-1',
        'base-1'
      )
    )
  }
})

void test('restored file labels must describe the same files in the original order', () => {
  const value = draft()
  value.files.reverse()
  assert.throws(() => parseManagedImportDraft(value, 'user-1', 'base-1'))
})

void test('storage namespaces keep user and knowledge-base boundaries unambiguous', () => {
  assert.notEqual(
    managedImportStorageKey('user:a', 'base'),
    managedImportStorageKey('user', 'a:base')
  )
  assert.notEqual(
    managedImportStorageKey('user/a', 'base'),
    managedImportStorageKey('user', 'a/base')
  )
})
