<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Alert as AAlert, Button as AButton, Empty as AEmpty, Spin as ASpin } from 'ant-design-vue'
import {
  AppstoreOutlined,
  RobotOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  PlusOutlined,
  UploadOutlined,
  RightOutlined,
  ReloadOutlined
} from '@ant-design/icons-vue'
import { identity } from '../../modules/identity/public.ts'
import { RequestScope } from '../../shared/http.ts'
import WorkspacePage from '../../shared/components/WorkspacePage.vue'
import { loadResourceCatalog } from '../resourceCatalog.ts'
import type { ResourceKind, CatalogSection } from '../resourceCatalog.ts'
const sections = ref<CatalogSection[]>([]),
  loading = ref(false),
  filter = ref<ResourceKind | ''>('')
const scope = new RequestScope()
const icons = {
  models: AppstoreOutlined,
  agents: RobotOutlined,
  knowledge: DatabaseOutlined,
  skills: ThunderboltOutlined
}
const entries = computed(() =>
  sections.value
    .filter((section) => !filter.value || filter.value === section.kind)
    .flatMap((section) => section.entries.map((entry) => ({ ...entry, label: section.label })))
)
const failures = computed(() => sections.value.filter((section) => section.error))
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
      ><AButton :loading="loading" @click="load"><ReloadOutlined />刷新</AButton></template
    >
    <ASpin :spinning="loading">
      <div class="console-overview__metrics">
        <RouterLink
          v-for="section in sections"
          :key="section.kind"
          :to="section.path"
          :aria-label="section.label + '数量'"
          class="console-metric"
        >
          <div>
            <span>{{ section.label }}</span
            ><strong>{{ section.total === null ? '暂不可用' : section.total }}</strong>
          </div>
          <span class="console-metric__icon"><component :is="icons[section.kind]" /></span>
        </RouterLink>
        <div
          v-for="index in loading && !sections.length ? 4 : 0"
          :key="index"
          class="console-metric is-loading"
        >
          <span>正在读取</span><strong>—</strong>
        </div>
      </div>
    </ASpin>
    <section class="console-overview__section">
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
    <section class="console-overview__section console-resource-panel">
      <header>
        <h2>资源</h2>
        <div class="console-resource-tabs" role="group" aria-label="资源类型">
          <button type="button" :class="{ active: !filter }" @click="filter = ''">全部</button
          ><button
            v-for="section in sections"
            :key="section.kind"
            type="button"
            :class="{ active: filter === section.kind }"
            @click="filter = section.kind"
          >
            {{ section.label }}
          </button>
        </div>
      </header>
      <AAlert
        v-for="failure in failures"
        :key="failure.kind"
        type="warning"
        :message="failure.label + '：' + failure.error"
        show-icon
        class="space-bottom"
      />
      <RouterLink
        v-for="entry in entries"
        :key="entry.kind + entry.id"
        :to="entry.path"
        class="console-resource-row"
        ><span class="console-resource-row__icon"><component :is="icons[entry.kind]" /></span
        ><span class="console-resource-row__name"
          ><strong>{{ entry.name }}</strong
          ><small>{{ entry.description || entry.label }}</small></span
        ><span class="console-resource-row__kind">{{ entry.label }}</span
        ><RightOutlined
      /></RouterLink>
      <AEmpty
        v-if="!entries.length && !loading"
        :description="failures.length ? '部分资源暂不可用，可刷新重试' : '暂无资源'"
      />
    </section>
  </WorkspacePage>
</template>
<style scoped>
.console-overview__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 28px;
}
.console-metric {
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  min-height: 104px;
  padding: 16px 0;
  color: var(--color-text);
}
.console-metric > div {
  display: grid;
  gap: 9px;
}
.console-metric span {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.console-metric strong {
  font-size: 28px;
  font-weight: 500;
  line-height: 1.2;
}
.console-metric .console-metric__icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  color: var(--main-color);
  font-size: 21px;
}
.console-overview__section {
  margin-top: 32px;
}
.console-overview__section h2 {
  margin: 0 0 16px;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.console-shortcuts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 24px;
}
.console-shortcuts a {
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  min-height: 86px;
  padding: 16px 0;
  border-bottom: 1px solid var(--gray-150);
  color: var(--color-text);
}
.console-shortcuts a > .anticon:first-child {
  color: var(--main-color);
  font-size: 21px;
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
.console-shortcuts a:hover strong,
.console-metric:hover strong {
  color: var(--main-color);
}
.console-resource-panel {
  padding-top: 4px;
}
.console-resource-panel > header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}
.console-resource-panel > header h2 {
  margin: 0;
}
.console-resource-tabs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.console-resource-tabs button {
  padding: 5px 12px;
  border: 0;
  border-radius: 5px;
  color: var(--color-text-secondary);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.console-resource-tabs button.active {
  color: var(--main-color);
  background: var(--main-50);
}
.console-resource-row {
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 74px;
  padding: 12px 0;
  border-bottom: 1px solid var(--gray-150);
  color: var(--color-text);
}
.console-resource-row:last-child {
  border-bottom: 0;
}
.console-resource-row__icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  color: var(--main-color);
  font-size: 18px;
}
.console-resource-row__name {
  display: grid;
  flex: 1;
  gap: 4px;
  min-width: 0;
}
.console-resource-row__name strong {
  font-size: 14px;
  font-weight: 500;
}
.console-resource-row__name small {
  overflow: hidden;
  color: var(--color-text-tertiary);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.console-resource-row__kind,
.console-resource-row > .anticon {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
@media (max-width: 1200px) {
  .console-shortcuts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .console-overview__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
  }
  .console-metric {
    text-decoration: none;
    min-height: 94px;
    padding: 12px 0;
  }
  .console-metric strong {
    font-size: 24px;
  }
  .console-shortcuts {
    gap: 10px;
  }
  .console-shortcuts a {
    text-decoration: none;
    padding: 12px 0;
    gap: 9px;
  }
  .console-shortcuts a > .anticon:last-child {
    display: none;
  }
  .console-shortcuts small {
    font-size: 11px;
  }
  .console-resource-row__kind {
    display: none;
  }
}
</style>

<style scoped>
.console-metric .console-metric__icon :deep(.anticon) {
  font-size: 21px;
  color: inherit;
}
</style>
