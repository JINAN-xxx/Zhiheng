<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <section class="keys-panel">
    <div class="subpage-actions">
      <div><strong>访问密钥</strong><span>每把密钥可独立设置模型授权与调用限额</span></div>
      <a-button v-if="canCreate" type="primary" aria-label="创建密钥" @click="$emit('create')"
        ><template #icon><KeyOutlined /></template>创建密钥</a-button
      >
    </div>
    <WorkspaceState
      v-if="!canList"
      kind="forbidden"
      title="无权查看访问密钥"
      message="当前账号缺少访问密钥列表权限。"
    />
    <WorkspaceState v-else-if="loading && !keys.length" kind="loading" />
    <WorkspaceState
      v-else-if="error && !keys.length"
      kind="error"
      :message="error"
      @retry="$emit('retry')"
    />
    <WorkspaceState
      v-else-if="!keys.length"
      kind="empty"
      title="暂无访问密钥"
      message="为当前应用创建第一把访问密钥。"
    />
    <div v-else class="key-list">
      <div v-if="error" class="stale-notice">刷新失败，当前访问密钥列表可能已过期。</div>
      <article v-for="item in keys" :key="item.keyId" class="key-row">
        <div class="key-row__identity">
          <div class="key-row__title">
            <strong>{{ item.keyName }}</strong>
            <WorkspaceStatus :status="item.status" :label="developerKeyStatusLabel(item.status)" />
          </div>
          <code>{{ item.maskedKey }}</code>
          <small
            >{{ item.grants.length }} 个模型授权 · 最近使用 {{ formatTime(item.lastUsedAt) }}</small
          >
        </div>
        <div class="key-row__quota">
          <span>每分钟请求数 {{ item.quota.rpmLimit ?? '不限' }}</span>
          <span>每分钟令牌数 {{ item.quota.tpmLimit ?? '不限' }}</span>
          <span>并发 {{ item.quota.concurrencyLimit ?? '不限' }}</span>
        </div>
        <WorkspaceMoreMenu
          v-if="(item.status === 'active' || item.status === 'retiring') && actions.length"
          :items="
            actions.filter((action) => item.status === 'active' || action.command === 'revoke')
          "
          @command="$emit('command', $event, item)"
        />
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Button as AButton } from 'ant-design-vue'
import { KeyOutlined } from '@ant-design/icons-vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
import { developerKeyStatusLabel } from '../presentation.ts'
import type { DeveloperKey } from '../api/index.ts'
withDefaults(
  defineProps<{
    keys: DeveloperKey[]
    loading?: boolean
    error?: string
    canList?: boolean
    canCreate?: boolean
    actions: MenuAction[]
  }>(),
  { loading: false, error: '', canList: false, canCreate: false }
)
defineEmits<{ create: []; retry: []; command: [command: string, item: DeveloperKey] }>()
function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '从未'
}
</script>

<style scoped lang="scss">
.subpage-actions,
.key-row__title,
.key-row__quota {
  display: flex;
  align-items: center;
  gap: 10px;
}

.subpage-actions {
  min-height: 64px;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--gray-150);
}

.subpage-actions > div {
  display: grid;
  gap: 3px;
}

.subpage-actions strong {
  font-size: 15px;
  font-weight: 600;
}
.subpage-actions > div > span {
  font-size: 13px;
}

.subpage-actions span,
.key-row small,
.key-row__quota {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.key-list {
  display: grid;
}

.key-row {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto 32px;
  align-items: center;
  gap: 16px;
  min-height: 82px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--gray-150);
}

.key-row__identity {
  display: grid;
  min-width: 0;
  gap: 5px;
}

.key-row code {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-family: var(--workspace-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stale-notice {
  padding: 8px 14px;
  color: var(--color-warning-900);
  background: var(--color-warning-50);
  font-size: 12px;
}
</style>
