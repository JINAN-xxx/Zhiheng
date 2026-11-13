import assert from 'node:assert/strict'
import { test } from 'node:test'
import { knowledgeInvitationPath } from '../../src/modules/knowledge/invitationLink.ts'
void test('pasted invitations resolve only to this site and keep the token in the fragment', () => {
  const origin = 'https://platform.example'
  const token = 'A'.repeat(43)
  assert.equal(
    knowledgeInvitationPath('  ' + origin + '/knowledge/join#' + token + ' ', origin),
    '/knowledge/join#' + token
  )
  assert.equal(
    knowledgeInvitationPath('/knowledge/join#' + token, origin),
    '/knowledge/join#' + token
  )
  for (const value of [
    '',
    'invalid',
    'https://outside.example/knowledge/join#' + token,
    origin + '/login#' + token,
    origin + '/knowledge/join?token=' + token,
    origin + '/knowledge/join#short',
    'https://user:pass@platform.example/knowledge/join#' + token
  ]) {
    assert.equal(knowledgeInvitationPath(value, origin), null)
  }
})
