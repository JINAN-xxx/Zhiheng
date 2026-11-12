<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  DatabaseOutlined,
  PlusOutlined,
  SearchOutlined,
  MoreOutlined,
  LinkOutlined,
  FileTextOutlined,
  LockOutlined,
  TeamOutlined
} from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Input as AInput,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal
} from 'ant-design-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { useRouter } from 'vue-router'
import { identity, hasAnyPermission } from '../../identity/public.ts'
import type { KnowledgeBase, KnowledgeBaseListItem } from '../api/index.ts'
import { knowledgeInvitationPath } from '../invitationLink.ts'
const props = defineProps<{
  databases: KnowledgeBaseListItem[]
  loading: boolean
  error: string
  graphAvailable?: boolean
  canManageStorage?: boolean
}>()
const emit = defineEmits<{
  refresh: []
  create: []
  edit: [base: KnowledgeBase]
  acl: [base: KnowledgeBase]
  remove: [base: KnowledgeBase]
  open: [base: KnowledgeBase, view: string]
}>()
const router = useRouter()
const joinOpen = ref(false),
  joinLink = ref(''),
  joinError = ref('')
function openJoin() {
  joinLink.value = ''
  joinError.value = ''
  joinOpen.value = true
}
function joinFromLink() {
  const destination = knowledgeInvitationPath(joinLink.value, window.location.origin)
  if (!destination) {
    joinError.value = '请输入本站有效的知识库邀请链接'
    return
  }
  joinOpen.value = false
  void router.push(destination)
}
const defaultsAccess = computed(() => hasAnyPermission(identity.value, 'system:configs:read'))
const search = ref(''),
  scope = ref('owned')
const ownedCount = computed(
  () => props.databases.filter((base) => base.owner_id === identity.value?.id).length
)
const sharedCount = computed(() => props.databases.length - ownedCount.value)
const filtered = computed(() =>
  props.databases.filter(
    (base) =>
      (scope.value === 'owned'
        ? base.owner_id === identity.value?.id
        : base.owner_id !== identity.value?.id) &&
      (base.name + ' ' + (base.availability === 'available' ? base.description : ''))
        .toLowerCase()
        .includes(search.value.trim().toLowerCase())
  )
)
function sharing(base: KnowledgeBase): string {
  return base.sharing_scope === 'users' ? '成员共享' : '仅自己'
}
</script>
<template>
  <section class="knowledge-overview">
    <WorkspaceToolbar class="knowledge-toolbar" title="知识库" subtitle="添加资料、检索和使用知识">
      <template #actions>
        <RefreshButton label="刷新知识库" :loading="loading" @click="emit('refresh')" />
        <a-button aria-label="加入知识库" @click="openJoin"
          ><LinkOutlined aria-hidden="true" />加入知识库</a-button
        >
        <a-button type="primary" aria-label="新建知识库" @click="emit('create')"
          ><PlusOutlined aria-hidden="true" />新建知识库</a-button
        >
        <a-dropdown v-if="defaultsAccess" :trigger="['click']" placement="bottomRight">
          <a-button type="text" class="knowledge-more" aria-label="知识库更多操作"
            ><MoreOutlined aria-hidden="true"
          /></a-button>
          <template #overlay>
            <a-menu>
              <a-menu-item key="defaults" @click="router.push('/knowledge/settings')"
                >默认配置</a-menu-item
              >
            </a-menu>
          </template>
        </a-dropdown>
      </template>
    </WorkspaceToolbar>
    <div class="knowledge-overview__content">
      <div class="knowledge-controls">
        <div class="knowledge-scopes" role="group" aria-label="知识库范围">
          <button
            type="button"
            aria-label="我创建的"
            :class="{ active: scope === 'owned' }"
            :aria-pressed="scope === 'owned'"
            @click="scope = 'owned'"
          >
            我创建的 <span class="knowledge-scope-count">{{ ownedCount }}</span>
          </button>
          <button
            type="button"
            aria-label="与我共享"
            :class="{ active: scope === 'shared' }"
            :aria-pressed="scope === 'shared'"
            @click="scope = 'shared'"
          >
            与我共享 <span class="knowledge-scope-count">{{ sharedCount }}</span>
          </button>
        </div>
        <a-input v-model:value="search" allow-clear aria-label="搜索知识库" placeholder="搜索知识库"
          ><template #prefix><SearchOutlined /></template
        ></a-input>
      </div>
      <WorkspaceState v-if="loading && !databases.length" kind="loading" />
      <WorkspaceState
        v-else-if="error && !databases.length"
        kind="error"
        :message="error"
        @retry="emit('refresh')"
      />
      <WorkspaceState
        v-else-if="!filtered.length"
        kind="empty"
        :title="
          search
            ? '没有匹配的知识库'
            : scope === 'owned'
              ? '还没有创建知识库'
              : '暂时没有与我共享的知识库'
        "
        :message="scope === 'owned' ? '新建知识库并添加资料，系统会自动处理' : ''"
      />
      <template v-else>
        <a-alert v-if="error" type="warning" :message="error" show-icon />
        <div class="knowledge-list">
          <article
            v-for="base in filtered"
            :key="base.id"
            class="knowledge-entry"
            :class="{ 'knowledge-entry--unavailable': base.availability !== 'available' }"
          >
            <div
              v-if="base.availability !== 'available'"
              class="knowledge-entry__body"
              aria-disabled="true"
            >
              <div class="knowledge-entry__heading">
                <span class="knowledge-icon" aria-hidden="true"><DatabaseOutlined /></span>
                <div class="knowledge-entry__copy">
                  <h2 :title="base.name">{{ base.name }}</h2>
                  <p class="knowledge-entry__owner">
                    所有者：{{ base.owner_name || base.owner_id }}
                  </p>
                </div>
              </div>
              <div class="knowledge-entry__metadata" role="status">
                {{ base.availability === 'owner_deleted' ? '创建者已删除' : '创建者已停用' }}
              </div>
            </div>
            <template v-else>
              <button
                type="button"
                class="knowledge-entry__body"
                :aria-label="'打开知识库：' + base.name"
                @click="emit('open', base, 'documents')"
              >
                <div class="knowledge-entry__heading">
                  <span class="knowledge-icon" aria-hidden="true"><DatabaseOutlined /></span>
                  <div class="knowledge-entry__copy">
                    <h2 :title="base.name">{{ base.name }}</h2>
                    <p v-if="base.description" :title="base.description">{{ base.description }}</p>
                    <p
                      v-if="scope === 'shared'"
                      class="knowledge-entry__owner"
                      :title="base.owner_name || base.owner_id"
                    >
                      所有者：{{ base.owner_name || base.owner_id }}
                    </p>
                  </div>
                </div>
                <div class="knowledge-entry__metadata">
                  <span class="knowledge-entry__count">
                    <FileTextOutlined aria-hidden="true" />
                    <span
                      ><strong>{{ base.document_count }}</strong> 份资料</span
                    >
                  </span>
                  <span class="knowledge-entry__sharing">
                    <TeamOutlined v-if="base.sharing_scope === 'users'" aria-hidden="true" />
                    <LockOutlined v-else aria-hidden="true" />
                    {{ sharing(base) }}
                  </span>
                </div>
              </button>
              <a-dropdown
                v-if="base.can_manage && base.owner_id === identity?.id"
                :trigger="['click']"
              >
                <a-button
                  type="text"
                  class="knowledge-entry__more"
                  :aria-label="'更多操作：' + base.name"
                  ><MoreOutlined aria-hidden="true"
                /></a-button>
                <template #overlay
                  ><a-menu>
                    <a-menu-item key="share" @click="emit('acl', base)">共享</a-menu-item>
                    <a-menu-item key="edit" @click="emit('edit', base)">名称与描述</a-menu-item>
                    <a-menu-item
                      key="delete"
                      :disabled="!!base.active_job_id"
                      danger
                      @click="emit('remove', base)"
                      >删除知识库</a-menu-item
                    >
                  </a-menu></template
                >
              </a-dropdown>
            </template>
          </article>
        </div>
      </template>
    </div>
    <AModal
      :open="joinOpen"
      title="加入知识库"
      centered
      :width="520"
      ok-text="查看邀请"
      cancel-text="关闭"
      @ok="joinFromLink"
      @cancel="joinOpen = false"
    >
      <p class="knowledge-join-help">粘贴对方发来的邀请链接，查看后确认加入。</p>
      <AAlert
        v-if="joinError"
        type="error"
        :message="joinError"
        show-icon
        class="knowledge-join-error"
      />
      <AInput
        v-model:value="joinLink"
        aria-label="知识库邀请链接"
        placeholder="粘贴完整邀请链接"
        @change="joinError = ''"
        @press-enter="joinFromLink"
      />
    </AModal>
  </section>
</template>
<style scoped>
.knowledge-entry--unavailable {
  color: var(--color-text-secondary);
  background: var(--bg-container);
  opacity: 0.65;
}
.knowledge-entry--unavailable .knowledge-entry__body {
  cursor: default;
}
.knowledge-entry--unavailable .knowledge-icon {
  color: inherit;
  background: transparent;
}

.knowledge-join-help {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.knowledge-join-error {
  margin-bottom: 12px;
}

.knowledge-overview {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 1368px;
  height: 100%;
  min-height: 0;
  min-width: 0;
  margin: 0 auto;
  padding: 0 24px;
  box-sizing: border-box;
  background: var(--bg-container);
}
.knowledge-overview .knowledge-toolbar {
  min-height: 64px;
  padding: 6px 0;
  gap: 16px;
}
.knowledge-toolbar :deep(.workspace-toolbar__title) {
  font-size: 24px;
  font-weight: 600;
  line-height: 36px;
}
.knowledge-toolbar :deep(.workspace-toolbar__subtitle) {
  margin-top: 4px;
  font-size: 13px;
}
.knowledge-toolbar :deep(.workspace-toolbar__actions) {
  gap: 10px;
}
.knowledge-toolbar :deep(.workspace-toolbar__actions .ant-btn:not(.workspace-refresh-button)) {
  height: 38px;
  border-radius: 7px;
  font-size: 13px;
  box-shadow: none;
}
.knowledge-toolbar :deep(.workspace-refresh-button),
.knowledge-toolbar .knowledge-more {
  width: 36px;
  min-width: 36px;
  height: 36px;
  flex: 0 0 36px;
  padding: 0;
  border-color: transparent;
  background: transparent;
  color: var(--color-text-secondary);
}
.knowledge-overview__content {
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: auto;
  padding: 24px 0 24px;
  container: knowledge-directory / inline-size;
}
.knowledge-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 49px;
  border-bottom: 1px solid var(--gray-150);
}
.knowledge-scopes {
  display: flex;
  align-self: stretch;
  gap: 30px;
  flex-shrink: 0;
}
.knowledge-scopes button {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 48px;
  margin-bottom: -1px;
  padding: 0 2px 14px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
}
.knowledge-scopes button.active {
  color: var(--main-color);
  border-bottom-color: var(--main-color);
  font-weight: 600;
}
.knowledge-scopes button:hover {
  color: var(--main-color);
}
.knowledge-scope-count {
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.knowledge-scopes .active .knowledge-scope-count {
  color: var(--main-500);
}
.knowledge-controls :deep(.ant-input-affix-wrapper) {
  width: 276px;
  height: 36px;
  margin-bottom: 12px;
  border-radius: 7px;
  font-size: 13px;
}
.knowledge-overview__content > :deep(.workspace-state),
.knowledge-overview__content > :deep(.ant-alert) {
  margin-top: 22px;
}
.knowledge-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-top: 22px;
}
.knowledge-entry {
  position: relative;
  min-width: 0;
  overflow: visible;
  border: 1px solid var(--gray-150);
  border-radius: 11px;
  background: var(--bg-container);
  transition: border-color 0.15s ease;
}
.knowledge-entry:not(.knowledge-entry--unavailable):hover,
.knowledge-entry:not(.knowledge-entry--unavailable):focus-within {
  border-color: var(--main-200);
}
.knowledge-entry__heading {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  padding-right: 28px;
}
.knowledge-icon {
  display: grid;
  place-items: center;
  flex: 0 0 33px;
  width: 33px;
  height: 33px;
  border-radius: 8px;
  background: var(--main-30);
  color: var(--main-color);
  font-size: 20px;
}
.knowledge-entry__body {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: space-between;
  gap: 20px;
  width: 100%;
  min-height: 138px;
  min-width: 0;
  border: 0;
  border-radius: 10px;
  padding: 22px 22px 21px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  color: inherit;
  font: inherit;
  box-sizing: border-box;
}
.knowledge-entry__copy {
  flex: 1;
  min-width: 0;
}
.knowledge-entry h2 {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
}
.knowledge-entry p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.knowledge-entry__metadata {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 20px;
}
.knowledge-entry__count,
.knowledge-entry__sharing {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.knowledge-entry__count :deep(.anticon),
.knowledge-entry__sharing :deep(.anticon) {
  color: var(--color-text-tertiary);
  font-size: 14px;
}
.knowledge-entry__count strong {
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.knowledge-entry .knowledge-entry__owner {
  font-size: 11px;
}
.knowledge-entry .knowledge-entry__more {
  position: absolute;
  top: 23px;
  right: 17px;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--color-text-secondary);
}
.knowledge-entry__body:focus-visible,
.knowledge-scopes button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 4px;
  border-radius: 7px;
}
@container knowledge-directory (max-width: 980px) {
  .knowledge-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@container knowledge-directory (max-width: 620px) {
  .knowledge-list {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 760px) {
  .knowledge-overview {
    padding: 0 16px;
  }
  .knowledge-overview .knowledge-toolbar {
    min-height: 0;
    padding: 8px 0;
  }
  .knowledge-toolbar :deep(.workspace-toolbar__leading),
  .knowledge-toolbar :deep(.workspace-toolbar__actions) {
    width: 100%;
  }
  .knowledge-toolbar :deep(.workspace-toolbar__actions) {
    justify-content: flex-start;
    gap: 8px;
  }
  .knowledge-toolbar :deep(.workspace-toolbar__actions .ant-btn:not(.workspace-refresh-button)) {
    height: 36px;
    padding: 0 10px;
    font-size: 12px;
  }
  .knowledge-toolbar :deep(.workspace-refresh-button),
  .knowledge-toolbar .knowledge-more {
    width: 32px;
    min-width: 32px;
    flex-basis: 32px;
    padding: 0;
  }
  .knowledge-overview__content {
    padding-top: 18px;
  }
  .knowledge-controls {
    align-items: stretch;
    flex-direction: column;
    gap: 14px;
    border-bottom: 0;
  }
  .knowledge-scopes {
    border-bottom: 1px solid var(--gray-150);
  }
  .knowledge-controls :deep(.ant-input-affix-wrapper) {
    width: 100%;
    margin-bottom: 0;
  }
  .knowledge-entry__body {
    padding: 20px;
  }
  .knowledge-entry .knowledge-entry__more {
    top: 21px;
    right: 15px;
  }
}
</style>
