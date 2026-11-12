<script setup lang="ts">
import { inject, computed, onBeforeUnmount, ref, watch } from 'vue'
import { Alert as AAlert, Button as AButton, Switch as ASwitch } from 'ant-design-vue'
import { adminReadCacheKey } from '../readCache.ts'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { createEntity, updateEntity, decodeConfig, getAllCollection } from '../api/index.ts'
import type { Config } from '../api/index.ts'
import { ApiError, RequestScope, errorMessage, isAborted } from '../../../shared/http.ts'
const readable = computed(() => hasAdminPermission(identity.value, 'configs'))
const writable = computed(() => hasAdminPermission(identity.value, 'configs', 'write'))
const readCache = inject(adminReadCacheKey, null)
const restoredSetting = readCache?.getResource<Config | null>('notice-visibility')
const current = ref<Config | null>(restoredSetting?.value ?? null),
  enabled = ref(!restoredSetting?.value?.is_active || restoredSetting.value.value !== 'false'),
  ready = ref(!!restoredSetting),
  loading = ref(false),
  saving = ref(false),
  error = ref('')
const reads = new RequestScope(),
  writes = new RequestScope()
const settingKey = 'console.notice_enabled'
async function refresh(): Promise<void> {
  if (!readable.value) return
  const ticket = reads.replace()
  loading.value = true
  error.value = ''
  try {
    const stamp = readCache?.stamp()
    const items = await getAllCollection(
      'configs',
      decodeConfig,
      { search: settingKey },
      ticket.signal
    )
    if (!ticket.isCurrent()) return
    current.value = items.find((item) => item.key === settingKey) ?? null
    enabled.value = !current.value?.is_active || current.value.value !== 'false'
    ready.value = true
    if (stamp) readCache?.setResource('notice-visibility', current.value, stamp)
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      ready.value = false
    }
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function update(value: unknown): Promise<void> {
  if (
    typeof value !== 'boolean' ||
    !writable.value ||
    !ready.value ||
    saving.value ||
    loading.value
  )
    return
  readCache?.invalidate()
  const stamp = readCache?.stamp()
  const ticket = writes.replace()
  saving.value = true
  error.value = ''
  const body = {
    key: settingKey,
    name: current.value?.name || '控制台公告显示',
    value: String(value),
    is_active: true,
    remark: current.value?.remark ?? ''
  }
  try {
    const result = current.value
      ? await updateEntity('configs', current.value, body, decodeConfig, ticket.signal)
      : await createEntity('configs', body, decodeConfig, ticket.signal)
    if (ticket.isCurrent()) {
      current.value = result
      enabled.value = value
      if (stamp) readCache?.setResource('notice-visibility', result, stamp)
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      if (reason instanceof ApiError && reason.status === 409) await refresh()
      else ready.value = false
      error.value =
        errorMessage(reason) +
        (reason instanceof ApiError && reason.status < 500
          ? ''
          : '；保存结果待确认，请刷新查看当前状态。')
    }
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
watch(
  readable,
  (value) => {
    if (value) {
      if (!readCache?.isFresh(restoredSetting)) void refresh()
    } else {
      reads.replace()
      writes.replace()
      current.value = null
      ready.value = false
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  reads.dispose()
  writes.dispose()
})
defineExpose({ refresh })
</script>
<template>
  <section v-if="readable" class="notice-visibility" aria-label="公告展示设置">
    <div class="notice-visibility__row">
      <div>
        <strong>在平台展示公告</strong>
        <p>开启后，已发布内容会显示在登录用户的通知面板中。</p>
      </div>
      <ASwitch
        :checked="enabled"
        :loading="loading || saving"
        :disabled="!writable || !ready || loading || saving"
        aria-label="在平台展示公告"
        @change="update"
      />
    </div>
    <div v-if="error" class="notice-visibility__error">
      <AAlert :message="error" type="warning" show-icon /><AButton
        size="small"
        :disabled="saving"
        @click="refresh"
        >刷新状态</AButton
      >
    </div>
  </section>
</template>
<style scoped>
.notice-visibility {
  padding: 16px 18px;
  border-radius: 10px;
  background: var(--gray-25);
}
.notice-visibility__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.notice-visibility strong {
  font-size: 13px;
  font-weight: 500;
}
.notice-visibility p {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.notice-visibility__error {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.notice-visibility__error .ant-btn {
  justify-self: start;
}
</style>
