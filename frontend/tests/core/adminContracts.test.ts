import test from 'node:test'
import assert from 'node:assert/strict'
import {
  decodePage,
  decodeRole,
  decodeSession,
  decodeUser,
  decodeUserOptions
} from '../../src/modules/admin/api/types.ts'
import { decodeMonitor } from '../../src/modules/admin/api/monitor.ts'

const roleRecord = {
  id: 'role',
  version: 1,
  code: 'super_admin',
  name: '超级管理员',
  permissions: [],
  is_active: true,
  remark: ''
}

void test('role contract retains system identity and member counts from the server', () => {
  const role = decodeRole({ ...roleRecord, system_key: 'super_admin', member_count: 2 })
  assert.equal(role.system_key, 'super_admin')
  assert.equal(role.member_count, 2)
  assert.throws(() => decodeRole({ ...roleRecord, system_key: true, member_count: 2 }))
  assert.throws(() => decodeRole({ ...roleRecord, system_key: null, member_count: '2' }))
})

void test('historical role responses receive ordinary role metadata defaults', () => {
  const role = decodeRole(roleRecord)
  assert.equal(role.system_key, null)
  assert.equal(role.member_count, 0)
})

void test('user role options retain system identity and support historical responses', () => {
  const options = decodeUserOptions({
    departments: [{ id: 'department', name: '总部' }],
    posts: [],
    roles: [
      { id: 'system-role', name: '超级管理员', system_key: 'super_admin' },
      { id: 'ordinary-role', name: '运营员' }
    ]
  })
  assert.deepEqual(options.roles, [
    { id: 'system-role', name: '超级管理员', system_key: 'super_admin' },
    { id: 'ordinary-role', name: '运营员', system_key: null }
  ])
  assert.deepEqual(options.departments, [{ id: 'department', name: '总部' }])
  assert.throws(() =>
    decodeUserOptions({
      departments: [],
      posts: [],
      roles: [{ id: 'role', name: '角色', system_key: 1 }]
    })
  )
})

void test('historical users and sessions accept missing dates while retaining real CAS versions', () => {
  const user = decodeUser({
    id: 'user',
    version: 2,
    username: 'alice',
    display_name: '',
    email: '',
    phone: '',
    department_id: null,
    post_ids: [],
    role_ids: [],
    is_admin: false,
    is_active: true,
    remark: '',
    created_at: null,
    updated_at: null,
    password: 'must-never-return'
  })
  assert.equal(user.created_at, undefined)
  assert.equal('password' in user, false)
  const session = decodeSession({
    session_id: 'session',
    user_id: 'user',
    username: 'alice',
    version: 5,
    created_at: null,
    expires_at: '2026-10-29T00:00:00Z',
    is_current: false
  })
  assert.equal(session.created_at, null)
  assert.equal(session.version, 5)
  assert.throws(() => decodeSession({ ...session, version: '5' }))
  assert.throws(() => decodePage(decodeUser)({ items: [user], total: '1', page: 1, page_size: 20 }))
})

void test('unavailable monitor measurements remain null and are not converted to zero', () => {
  const result = decodeMonitor({
    platform: {
      python_version: null,
      system: null,
      cpu_count: null,
      rss_bytes: null,
      disk_total_bytes: null,
      disk_used_bytes: null
    },
    database: { status: 'unavailable', version: null, size_bytes: null, connections: null },
    redis: {
      status: 'unavailable',
      used_memory_bytes: null,
      connected_clients: null,
      uptime_seconds: null,
      db_keys: null
    },
    worker: { status: 'unavailable', heartbeat_age_seconds: null },
    schema_version: null
  })
  assert.equal(result.database.connections, null)
  assert.equal(result.redis.db_keys, null)
  assert.throws(() =>
    decodeMonitor({ ...result, worker: { status: 'healthy', heartbeat_age_seconds: 0 } })
  )
})
