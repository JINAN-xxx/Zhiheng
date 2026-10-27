<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  DatePicker as ADatePicker,
  RadioGroup as ARadioGroup,
  Select as ASelect,
  Space as ASpace,
  Tag as ATag
} from 'ant-design-vue'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { findKnowledgeUsers, getBaseAcl, saveBaseAcl } from '../api/index.ts'
import type { KnowledgeAcl, KnowledgeBase, KnowledgeUser } from '../api/index.ts'
const emit = defineEmits<{ refresh: [] }>()
interface Entry {
  key: string
  userId: string | undefined
  effect: 'allow' | 'deny'
  expiresAt: string
  users: KnowledgeUser[]
  loading: boolean
}
const visible = ref(false),
  loading = ref(false),
  saving = ref(false),
  uncertain = ref(false),
  error = ref('')
const access = ref<'private' | 'public'>('private'),
  acl = ref<KnowledgeAcl | null>(null),
  entries = ref<Entry[]>([])
const scope = new RequestScope(),
  writeScope = new RequestScope(),
  searches = new Map<string, RequestScope>()
function close(): void {
  scope.replace()
  writeScope.replace()
  for (const search of searches.values()) search.dispose()
  searches.clear()
  visible.value = false
  loading.value = false
  saving.value = false
  entries.value = []
  acl.value = null
}
function localDate(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}
async function open(base: KnowledgeBase): Promise<void> {
  close()
  visible.value = true
  loading.value = true
  uncertain.value = false
  error.value = ''
  const ticket = scope.replace()
  try {
    const value = await getBaseAcl(base.id, ticket.signal)
    if (!ticket.isCurrent()) return
    acl.value = value
    access.value = value.base.access
    entries.value = value.entries.map((entry) => ({
      key: crypto.randomUUID(),
      userId: entry.user_id,
      effect: entry.effect,
      expiresAt: localDate(entry.expires_at),
      users: [],
      loading: false
    }))
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function addEntry(): void {
  entries.value.push({
    key: crypto.randomUUID(),
    userId: undefined,
    effect: 'allow',
    expiresAt: '',
    users: [],
    loading: false
  })
}
function removeEntry(row: Entry): void {
  searches.get(row.key)?.dispose()
  searches.delete(row.key)
  entries.value = entries.value.filter((entry) => entry.key !== row.key)
}
function options(row: Entry) {
  const users = new Map(row.users.map((user) => [user.id, user.username]))
  if (row.userId && !users.has(row.userId)) users.set(row.userId, row.userId)
  return [...users].map(([value, label]) => ({ value, label }))
}
function setUser(row: Entry, value: unknown): void {
  row.userId = typeof value === 'string' ? value : undefined
}
async function searchUsers(row: Entry, search: string): Promise<void> {
  if (!acl.value) return
  let owner = searches.get(row.key)
  if (!owner) {
    owner = new RequestScope()
    searches.set(row.key, owner)
  }
  const ticket = owner.replace(),
    id = acl.value.base.id
  row.loading = true
  try {
    const users = await findKnowledgeUsers(id, search, ticket.signal)
    if (ticket.isCurrent() && visible.value) row.users = users
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) row.loading = false
  }
}
async function save(): Promise<void> {
  if (!acl.value || saving.value || loading.value || uncertain.value) return
  if (
    entries.value.some((row) => !row.userId) ||
    new Set(entries.value.map((row) => row.userId)).size !== entries.value.length
  ) {
    error.value = '请选择完整且不重复的授权用户'
    return
  }
  let body
  try {
    body = {
      expected_version: acl.value.base.acl_version,
      access: access.value,
      entries: entries.value.map((row) => ({
        user_id: row.userId!,
        effect: row.effect,
        expires_at: row.expiresAt ? new Date(row.expiresAt.replace(' ', 'T')).toISOString() : null
      }))
    }
  } catch {
    error.value = '有效期格式无效'
    return
  }
  const ticket = writeScope.replace(),
    id = acl.value.base.id
  saving.value = true
  error.value = ''
  try {
    const value = await saveBaseAcl(id, body, ticket.signal)
    if (ticket.isCurrent()) {
      acl.value = value
      emit('refresh')
      close()
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      uncertain.value = !(reason instanceof ApiError) || reason.status >= 500
    }
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  writeScope.dispose()
  for (const search of searches.values()) search.dispose()
})
defineExpose({ open, close })
</script>
<template>
  <ResourceDrawer
    :open="visible"
    :title="'知识库权限 - ' + (acl?.base.name ?? '')"
    :width="900"
    @close="close"
  >
    <a-alert v-if="error" class="acl-alert" type="error" :message="error" show-icon />
    <a-alert
      class="acl-alert"
      type="info"
      message="原文权限与知识库权限分别生效。允许访问知识库的用户，仍需具备各原文的读取权限才能检索和查看对应内容。"
      show-icon
    />
    <a-alert
      v-if="uncertain"
      class="acl-alert"
      type="warning"
      message="结果尚未确认，请重新打开权限面板核对后再修改。"
      show-icon
    />
    <template v-if="acl">
      <h3>访问范围</h3>
      <a-radio-group
        v-model:value="access"
        :disabled="saving || uncertain"
        :options="[
          { label: '私有', value: 'private' },
          { label: '已登录用户可读', value: 'public' }
        ]"
      />
      <h3>内置权限</h3>
      <a-space wrap
        ><a-tag v-for="rule in acl.builtin_permissions" :key="rule.source"
          >{{
            rule.source === 'admin'
              ? '平台管理员'
              : rule.source === 'owner'
                ? '知识库所有者'
                : rule.source
          }}
          · {{ rule.enabled ? '允许' : '停用' }} ·
          {{ rule.deny_overridable ? '可覆盖' : '不可覆盖' }}</a-tag
        ></a-space
      >
      <div class="acl-header">
        <h3>用户授权</h3>
        <a-button :disabled="saving || uncertain" @click="addEntry"
          ><PlusOutlined />添加授权</a-button
        >
      </div>
      <div v-for="row in entries" :key="row.key" class="acl-entry">
        <a-select
          :value="row.userId"
          aria-label="授权用户"
          show-search
          :filter-option="false"
          :loading="row.loading"
          :options="options(row)"
          :disabled="saving || uncertain"
          placeholder="搜索用户"
          @search="searchUsers(row, $event)"
          @update:value="setUser(row, $event)"
        />
        <a-select
          :value="row.effect"
          aria-label="授权规则"
          :disabled="saving || uncertain"
          :options="[
            { label: '允许访问', value: 'allow' },
            { label: '拒绝访问', value: 'deny' }
          ]"
          @update:value="row.effect = $event === 'deny' ? 'deny' : 'allow'"
        />
        <a-date-picker
          :value="row.expiresAt"
          aria-label="有效期至"
          show-time
          value-format="YYYY-MM-DD HH:mm:ss"
          placeholder="永久有效"
          :disabled="saving || uncertain"
          @update:value="row.expiresAt = typeof $event === 'string' ? $event : ''"
        />
        <a-button
          danger
          :disabled="saving || uncertain"
          aria-label="移除授权"
          @click="removeEntry(row)"
          ><DeleteOutlined
        /></a-button>
      </div>
      <p>拒绝规则优先于公开访问和允许规则；所有者与管理员的内置权限不可被覆盖。</p>
    </template>
    <template #footer
      ><a-button @click="close">取消</a-button
      ><a-button
        type="primary"
        :loading="saving"
        :disabled="!acl || loading || uncertain"
        @click="save"
        >保存</a-button
      ></template
    >
  </ResourceDrawer>
</template>
<style scoped>
.acl-alert {
  margin-bottom: 16px;
}
.acl-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
}
.acl-entry {
  display: grid;
  grid-template-columns: minmax(150px, 1fr) 120px minmax(190px, 1fr) 36px;
  gap: 10px;
  margin-bottom: 10px;
}
p {
  color: var(--color-text-secondary);
  font-size: 13px;
}
@media (max-width: 600px) {
  .acl-entry {
    grid-template-columns: minmax(0, 1fr) 36px;
  }
  .acl-entry > :nth-child(1),
  .acl-entry > :nth-child(3) {
    grid-column: 1;
  }
  .acl-entry > :nth-child(2) {
    grid-column: 1;
  }
  .acl-entry > :nth-child(4) {
    grid-column: 2;
    grid-row: 1;
  }
}
</style>
