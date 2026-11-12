import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseProfile } from '../../src/modules/identity/api/profile.ts'
import {
  profileUpdate,
  validatePasswordDraft,
  validateProfileDraft
} from '../../src/modules/identity/profileForm.ts'

const profile = {
  id: 'member-1',
  username: 'member',
  display_name: '成员',
  email: '',
  phone: '',
  is_admin: false,
  department_name: null,
  roles: ['成员'],
  created_at: null,
  version: 1
}

void test('profile contract accepts nullable metadata and rejects invalid field types', () => {
  assert.deepEqual(parseProfile(profile), profile)
  assert.deepEqual(parseProfile({ ...profile, posts: ['历史岗位'] }), profile)
  for (const invalid of [
    { ...profile, id: 1 },
    { ...profile, display_name: null },
    { ...profile, is_admin: 'false' },
    { ...profile, department_name: 1 },
    { ...profile, roles: [1] },
    { ...profile, created_at: 1 },
    { ...profile, version: 1.5 },
    { ...profile, version: -1 }
  ]) {
    assert.throws(() => parseProfile(invalid))
  }
})

void test('profile updates trim editable contact fields and keep account privileges out', () => {
  const draft = { display_name: ' 成员 ', email: ' member@example.com ', phone: ' 123 ' }
  assert.deepEqual(validateProfileDraft(draft), {})
  assert.deepEqual(profileUpdate(draft, 3), {
    display_name: '成员',
    email: 'member@example.com',
    phone: '123',
    expected_version: 3
  })
  assert.deepEqual(validateProfileDraft({ display_name: '', email: '', phone: '' }), {})
})

void test('profile form enforces field bounds and an optional contact email', () => {
  assert.deepEqual(
    Object.keys(
      validateProfileDraft({
        display_name: '名'.repeat(101),
        email: 'not-an-email',
        phone: '1'.repeat(41)
      })
    ).sort(),
    ['display_name', 'email', 'phone']
  )
  assert.deepEqual(
    validateProfileDraft({ display_name: '名'.repeat(100), email: '', phone: '1'.repeat(40) }),
    {}
  )
})

void test('password confirmation and length are checked without trimming passwords', () => {
  assert.deepEqual(
    validatePasswordDraft({
      current_password: 'old',
      new_password: '🔐'.repeat(12),
      confirmation: '🔐'.repeat(12)
    }),
    {}
  )
  assert.deepEqual(
    validatePasswordDraft({
      current_password: 'old',
      new_password: ' '.repeat(12),
      confirmation: ' '.repeat(12)
    }),
    {}
  )
  assert.ok(
    validatePasswordDraft({
      current_password: '',
      new_password: 'short',
      confirmation: 'different'
    }).current_password
  )
  assert.ok(
    validatePasswordDraft({
      current_password: 'old',
      new_password: '🔐'.repeat(11),
      confirmation: '🔐'.repeat(11)
    }).new_password
  )
  assert.ok(
    validatePasswordDraft({
      current_password: 'old',
      new_password: 'x'.repeat(1025),
      confirmation: 'x'.repeat(1025)
    }).new_password
  )
  assert.ok(
    validatePasswordDraft({
      current_password: 'old',
      new_password: 'x'.repeat(12),
      confirmation: 'different'
    }).confirmation
  )
})
