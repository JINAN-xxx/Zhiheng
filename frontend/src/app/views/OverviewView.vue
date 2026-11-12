<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Alert as AAlert, Spin as ASpin } from 'ant-design-vue'
import {
  AppstoreOutlined,
  RobotOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  PlusOutlined,
  UploadOutlined,
  RightOutlined
} from '@ant-design/icons-vue'
import { identity } from '../../modules/identity/public.ts'
import { RequestScope } from '../../shared/http.ts'
import WorkspacePage from '../../shared/components/WorkspacePage.vue'
import RefreshButton from '../../shared/components/RefreshButton.vue'
import { loadResourceCatalog } from '../resourceCatalog.ts'
import type { CatalogSection } from '../resourceCatalog.ts'
const sections = ref<CatalogSection[]>([]),
  loading = ref(false)
const scope = new RequestScope()
const previewLimit = 3
const icons = {
  models: AppstoreOutlined,
  agents: RobotOutlined,
  knowledge: DatabaseOutlined,
  skills: ThunderboltOutlined
}
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  const result = await loadResourceCatalog(context.signal, '', identity.value?.is_admin ?? false)
  if (context.isCurrent()) {
    sections.value = result
    loading.value = false
  }
}
onMounted(() => void load())
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <WorkspacePage title="工作台">
    <template #actions
      ><RefreshButton label="刷新工作台" :loading="loading" @click="load"
    /></template>
    <section class="console-shortcuts-section">
      <h2>快捷入口</h2>
      <div class="console-shortcuts">
        <RouterLink to="/chat"
          ><MessageOutlined /><span><strong>开始对话</strong><small>使用模型处理问题</small></span
          ><RightOutlined
        /></RouterLink>
        <RouterLink to="/agents?action=create"
          ><PlusOutlined /><span><strong>创建智能体</strong><small>配置专属助手</small></span
          ><RightOutlined
        /></RouterLink>
        <RouterLink to="/skills?action=upload"
          ><UploadOutlined /><span
            ><strong>上传技能</strong><small>导入 SKILL.md 或 ZIP 包</small></span
          ><RightOutlined
        /></RouterLink>
        <RouterLink to="/knowledge?action=create"
          ><DatabaseOutlined /><span
            ><strong>创建知识库</strong><small>管理业务知识与资料</small></span
          ><RightOutlined
        /></RouterLink>
      </div>
    </section>
    <section class="console-resource-panel" aria-label="资源总览" :aria-busy="loading">
      <h2>资源</h2>
      <ASpin :spinning="loading">
        <div class="console-resource-grid">
          <section
            v-for="section in sections"
            :key="section.kind"
            class="console-resource-group"
            :aria-label="section.label + '资源'"
          >
            <RouterLink
              class="console-resource-group__header"
              :to="section.path"
              :aria-label="'查看全部' + section.label"
            >
              <span class="console-resource-group__icon"
                ><component :is="icons[section.kind]" aria-hidden="true"
              /></span>
              <h3>{{ section.label }}</h3>
              <span class="console-resource-count">{{
                section.total === null ? '暂不可用' : section.total
              }}</span>
              <span class="console-resource-more"
                >查看全部<RightOutlined aria-hidden="true"
              /></span>
            </RouterLink>
            <AAlert
              v-if="section.error"
              :message="section.error"
              type="warning"
              show-icon
              class="console-resource-error"
            />
            <div v-else-if="section.entries.length" class="console-resource-items">
              <RouterLink
                v-for="entry in section.entries.slice(0, previewLimit)"
                :key="entry.id"
                :to="entry.path"
                :aria-label="entry.name"
                class="console-resource-row"
              >
                <span class="console-resource-row__name">
                  <strong :title="entry.name">{{ entry.name }}</strong>
                  <small v-if="entry.description" :title="entry.description">{{
                    entry.description
                  }}</small>
                </span>
                <RightOutlined aria-hidden="true" />
              </RouterLink>
            </div>
            <p v-else class="console-resource-empty">
              {{ loading ? '正在读取资源…' : '暂无' + section.label }}
            </p>
          </section>
          <div
            v-for="index in loading && !sections.length ? 4 : 0"
            :key="'loading-' + index"
            class="console-resource-group console-resource-skeleton"
            aria-hidden="true"
          >
            <span /><span /><span />
          </div>
        </div>
      </ASpin>
    </section>
  </WorkspacePage>
</template>
<style scoped>
.console-shortcuts-section h2,
.console-resource-panel > h2 {
  margin: 0 0 12px;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.console-shortcuts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.console-shortcuts a {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 72px;
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  color: var(--color-text);
  background: var(--bg-container);
  text-decoration: none;
}
.console-shortcuts a > .anticon:first-child {
  color: var(--main-color);
  font-size: 18px;
}
.console-shortcuts a > span:not(.anticon) {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.console-shortcuts strong {
  font-size: 14px;
  font-weight: 500;
}
.console-shortcuts small {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.console-shortcuts a > .anticon:last-child {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.console-shortcuts a:hover {
  border-color: var(--main-200);
}
.console-resource-panel {
  margin-top: 24px;
}
.console-resource-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.console-resource-group {
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.console-resource-group__header {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  margin-bottom: 4px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--gray-150);
  color: var(--color-text);
  text-decoration: none;
}
.console-resource-group__header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}
.console-resource-group__icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  place-items: center;
  border-radius: 7px;
  color: var(--main-color);
  background: var(--main-50);
  font-size: 16px;
}
.console-resource-count {
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--color-text-secondary);
  background: var(--gray-50);
  font-size: 12px;
  white-space: nowrap;
}
.console-resource-more {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: 12px;
  white-space: nowrap;
}
.console-resource-more .anticon {
  font-size: 10px;
}
.console-resource-group__header:hover,
.console-resource-group__header:hover .console-resource-more {
  color: var(--main-color);
}
.console-resource-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  min-height: 52px;
  padding: 8px 4px;
  border-bottom: 1px solid var(--gray-150);
  border-radius: 4px;
  color: var(--color-text);
  text-decoration: none;
}
.console-resource-row:last-child {
  border-bottom-color: transparent;
}
.console-resource-row:hover {
  background: var(--gray-25);
}
.console-resource-row__name {
  display: grid;
  flex: 1;
  gap: 3px;
  min-width: 0;
}
.console-resource-row__name strong,
.console-resource-row__name small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.console-resource-row__name strong {
  font-size: 14px;
  font-weight: 400;
}
.console-resource-row__name small,
.console-resource-row > .anticon {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.console-resource-error {
  margin-top: 12px;
}
.console-resource-empty {
  margin: 0;
  padding: 28px 4px;
  color: var(--color-text-tertiary);
  font-size: 13px;
}
.console-resource-skeleton {
  min-height: 156px;
}
.console-resource-skeleton span {
  display: block;
  width: 65%;
  height: 12px;
  margin: 16px 0;
  border-radius: 4px;
  background: var(--gray-50);
}
.console-resource-skeleton span:first-child {
  width: 30%;
}
.console-shortcuts a:focus-visible,
.console-resource-group__header:focus-visible,
.console-resource-row:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
@media (max-width: 1200px) {
  .console-shortcuts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .console-resource-grid {
    grid-template-columns: 1fr;
  }
  .console-shortcuts {
    gap: 10px;
  }
  .console-shortcuts a {
    gap: 8px;
  }
  .console-shortcuts a > .anticon:last-child {
    display: none;
  }
  .console-shortcuts small {
    font-size: 11px;
  }
}
</style>
