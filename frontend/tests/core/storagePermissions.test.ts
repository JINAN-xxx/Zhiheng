import assert from 'node:assert/strict'
import test from 'node:test'
import { canFileAction, canSelectFile, canLookupFileUsers } from '../../src/modules/files/access.ts'
import type { FileAction } from '../../src/modules/files/access.ts'

const file = { ownerUserId: 'owner', status: 'active' as const, sourceFileId: null }
const base = ['ai:storage:enabled', 'ai:storage:read']

void test('storage reader cannot select files for writes or load the user directory', () => {
  const reader = { id: 'owner', is_admin: false, permissions: base }
  assert.equal(canSelectFile(reader, file), false)
  assert.equal(canLookupFileUsers(reader), false)
  for (const action of ['delete', 'maintain', 'acl', 'transfer'] as FileAction[])
    assert.equal(canFileAction(reader, file, action), false)
})

void test('storage writes remain independent and never bypass file ownership', () => {
  for (const action of ['delete', 'maintain', 'acl', 'transfer'] as FileAction[]) {
    const user = { id: 'owner', is_admin: false, permissions: [...base, `ai:storage:${action}`] }
    for (const other of ['delete', 'maintain', 'acl', 'transfer'] as FileAction[])
      assert.equal(canFileAction(user, file, other), action === other)
    assert.equal(canFileAction({ ...user, id: 'another' }, file, action), false)
  }
  assert.equal(canSelectFile({ id: 'admin', is_admin: true }, file), false)
  const maintainer = { id: 'owner', is_admin: false, permissions: [...base, 'ai:storage:maintain'] }
  assert.equal(canSelectFile(maintainer, file), false)
  assert.equal(canSelectFile(maintainer, { ...file, status: 'deleted' }), true)
  assert.equal(
    canSelectFile(maintainer, { ...file, status: 'deleted', sourceFileId: 'source' }),
    false
  )
})
