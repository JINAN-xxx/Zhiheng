<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Button as AButton, Input as AInput, Popover as APopover } from 'ant-design-vue'
import { CheckOutlined, DatabaseOutlined } from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { availableBases } from '../api/index.ts'
import type { KnowledgeBase } from '../api/index.ts'
const props = defineProps<{ modelValue: KnowledgeBase[]; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: KnowledgeBase[]] }>()
const open = ref(false),
  loading = ref(false),
  error = ref(''),
  keyword = ref(''),
  rows = ref<KnowledgeBase[]>([])
const scope = new RequestScope()
const filtered = computed(() =>
  rows.value.filter((base) =>
    `${base.name} ${base.description}`.toLowerCase().includes(keyword.value.trim().toLowerCase())
  )
)
const selected = computed(() => new Set(props.modelValue.map((base) => base.id)))
async function load(): Promise<void> {
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await availableBases(ticket.signal)
    if (ticket.isCurrent() && open.value) rows.value = result
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function toggle(base: KnowledgeBase): void {
  if (props.disabled) return
  if (selected.value.has(base.id))
    emit(
      'update:modelValue',
      props.modelValue.filter((item) => item.id !== base.id)
    )
  else if (props.modelValue.length < 20) emit('update:modelValue', [...props.modelValue, base])
  else error.value = '每条消息最多选择 20 个知识库'
}
watch(open, (value) => {
  if (value && !props.disabled) void load()
  else {
    scope.replace()
    loading.value = false
    rows.value = []
    error.value = ''
    keyword.value = ''
  }
})
watch(
  () => props.disabled,
  (value) => {
    if (value) open.value = false
  }
)
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <a-popover v-model:open="open" trigger="click" placement="topLeft">
    <template #content
      ><div class="knowledge-picker-panel">
        <div class="knowledge-picker-header">
          <strong>选择知识库</strong><span>最多选择 20 个</span>
        </div>
        <a-input
          v-model:value="keyword"
          size="small"
          placeholder="搜索知识库"
          aria-label="搜索可用知识库"
          allow-clear
        />
        <div class="knowledge-picker-list">
          <WorkspaceState v-if="loading" kind="loading" /><WorkspaceState
            v-else-if="error"
            kind="error"
            :message="error"
            @retry="load"
          /><WorkspaceState v-else-if="!filtered.length" kind="empty" title="暂无可用知识库" />
          <button
            v-for="base in filtered"
            v-else
            :key="base.id"
            class="knowledge-option"
            :class="{ 'is-selected': selected.has(base.id) }"
            type="button"
            :aria-label="'选择知识库：' + base.name"
            :aria-pressed="selected.has(base.id)"
            @click="toggle(base)"
          >
            <DatabaseOutlined /><span
              ><strong>{{ base.name }}</strong
              ><small>{{ base.description || '暂无说明' }}</small></span
            ><CheckOutlined v-if="selected.has(base.id)" />
          </button>
        </div>
        <div class="knowledge-picker-footer">
          <span>已选择 {{ modelValue.length }} 个</span
          ><a-button type="link" size="small" aria-label="完成选择知识库" @click="open = false"
            >完成</a-button
          >
        </div>
      </div></template
    >
    <a-button type="text" :disabled="disabled" aria-label="选择知识库" title="选择知识库"
      ><DatabaseOutlined
    /></a-button>
  </a-popover>
</template>
<style scoped>
.knowledge-picker-panel {
  width: min(340px, calc(100vw - 56px));
  color: var(--color-text);
}
.knowledge-picker-header,
.knowledge-picker-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.knowledge-picker-header {
  margin-bottom: 10px;
}
.knowledge-picker-header span,
.knowledge-picker-footer {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.knowledge-picker-list {
  min-height: 150px;
  max-height: 280px;
  margin-top: 12px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
}
.knowledge-option {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 64px;
  padding: 12px;
  border: 0;
  border-bottom: 1px solid var(--gray-100);
  background: var(--bg-container);
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
}
.knowledge-option:hover {
  background: var(--gray-50);
}
.knowledge-option.is-selected {
  background: var(--main-50);
}
.knowledge-option > span {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 2px;
}
.knowledge-option strong,
.knowledge-option small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.knowledge-option small {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.knowledge-picker-footer {
  min-height: 34px;
  padding-top: 6px;
}
</style>
