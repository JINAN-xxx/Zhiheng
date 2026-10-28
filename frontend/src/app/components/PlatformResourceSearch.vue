<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  Alert as AAlert,
  Input as AInput,
  Modal as AModal,
  Spin as ASpin,
  Empty as AEmpty
} from 'ant-design-vue'
import { SearchOutlined, RightOutlined } from '@ant-design/icons-vue'
import { identity } from '../../modules/identity/public.ts'
import { RequestScope } from '../../shared/http.ts'
import { loadResourceCatalog } from '../resourceCatalog.ts'
import type { CatalogSection } from '../resourceCatalog.ts'
const open = ref(false),
  keyword = ref(''),
  loading = ref(false),
  sections = ref<CatalogSection[]>([])
const input = ref<{ focus: () => void } | null>(null),
  scope = new RequestScope()
let debounce: ReturnType<typeof setTimeout> | undefined
const hasResults = computed(() => sections.value.some((section) => section.entries.length))
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  const result = await loadResourceCatalog(
    context.signal,
    keyword.value,
    identity.value?.is_admin ?? false
  )
  if (context.isCurrent()) {
    sections.value = result
    loading.value = false
  }
}
watch(open, async (value) => {
  clearTimeout(debounce)
  if (!value) {
    scope.replace()
    loading.value = false
    return
  }
  keyword.value = ''
  sections.value = []
  void load()
  await nextTick()
  input.value?.focus()
})
watch(keyword, () => {
  clearTimeout(debounce)
  if (open.value) debounce = setTimeout(() => void load(), 200)
})
function keyboard(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    open.value = !open.value
  }
}
onMounted(() => window.addEventListener('keydown', keyboard))
onBeforeUnmount(() => {
  scope.dispose()
  clearTimeout(debounce)
  window.removeEventListener('keydown', keyboard)
})
</script>
<template>
  <button type="button" class="platform-search-trigger" aria-label="搜索资源" @click="open = true">
    <SearchOutlined /><span>搜索模型、智能体、知识库</span><kbd>Ctrl K</kbd>
  </button>
  <AModal v-model:open="open" title="搜索资源" :footer="null" :width="620" :destroy-on-close="true">
    <AInput
      ref="input"
      v-model:value="keyword"
      allow-clear
      placeholder="输入资源名称"
      aria-label="搜索资源名称"
      ><template #prefix><SearchOutlined /></template
    ></AInput>
    <ASpin :spinning="loading"
      ><div class="platform-search-results">
        <section v-for="section in sections" :key="section.kind">
          <template v-if="section.entries.length || section.error"
            ><h3>{{ section.label }}</h3>
            <AAlert v-if="section.error" type="warning" :message="section.error" />
            <RouterLink
              v-for="entry in section.entries"
              :key="entry.id"
              :to="entry.path"
              :aria-label="entry.name"
              @click="open = false"
              ><span
                ><strong>{{ entry.name }}</strong
                ><small>{{ entry.description }}</small></span
              ><RightOutlined
            /></RouterLink>
          </template>
        </section>
        <AEmpty
          v-if="!loading && !hasResults && !sections.some((section) => section.error)"
          description="没有匹配的资源"
        /></div
    ></ASpin>
  </AModal>
</template>
<style scoped>
.platform-search-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  width: 260px;
  padding: 6px 9px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  color: var(--color-text-tertiary);
  background: var(--bg-container);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.platform-search-trigger kbd {
  margin-left: auto;
  padding: 0 3px;
  border: 1px solid var(--gray-150);
  border-radius: 3px;
  font: inherit;
  white-space: nowrap;
}
.platform-search-results {
  min-height: 100px;
  max-height: 55vh;
  overflow-y: auto;
  padding-top: 14px;
}
.platform-search-results h3 {
  margin: 12px 0 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 400;
}
.platform-search-results a {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px;
  border-radius: 6px;
  color: var(--color-text);
}
.platform-search-results a:hover {
  background: var(--main-50);
}
.platform-search-results a > span:first-child {
  display: grid;
  min-width: 0;
  gap: 3px;
}
.platform-search-results strong {
  font-size: 14px;
  font-weight: 500;
}
.platform-search-results small {
  overflow: hidden;
  color: var(--color-text-tertiary);
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 760px) {
  .platform-search-trigger {
    width: 32px;
    justify-content: center;
    border: 0;
    padding: 0;
    font-size: 17px;
  }
  .platform-search-trigger > span:not(.anticon),
  .platform-search-trigger kbd {
    display: none;
  }
}
</style>
