<template>
  <header class="platform-topbar" :class="{ 'has-workspace-return': mode !== 'ai' && canOpenAi }">
    <button ref="menuButtonRef" type="button" class="platform-topbar__menu" aria-label="打开主菜单" title="打开主菜单" @click="emit('toggle-sidebar')">
      <MenuOutlined />
    </button>

    <div class="platform-topbar__context">
      <strong>{{ currentTitle }}</strong>
    </div>

    <div class="platform-topbar__actions">
      <button
        v-if="mode !== 'ai' && canOpenAi"
        type="button"
        class="platform-topbar__mode"
        title="返回 AI 工作台"
        aria-label="返回 AI 工作台"
        @click="emit('open-ai')"
      >
        <ArrowLeftOutlined />
        <span>返回 AI 工作台</span>
      </button>
      <PlatformAccountMenu compact @set-layout="emit('set-layout')" />
    </div>
  </header>
</template>

<script setup>
import { ArrowLeftOutlined, MenuOutlined } from '@ant-design/icons-vue'
import PlatformAccountMenu from './PlatformAccountMenu.vue'

const props = defineProps({
  mode: { type: String, required: true },
  canOpenAi: { type: Boolean, default: false },
})
const emit = defineEmits(['open-ai', 'set-layout', 'toggle-sidebar'])
const route = useRoute()
const menuButtonRef = ref(null)
const aiTitles = Object.freeze({
  '/ai/chat': '智能对话',
  '/ai/chat/work': '智能对话',
  '/ai/knowledge': '知识库',
  '/ai/model': '模型中心',
  '/ai/application': '开发接入',
  '/ai/usage': '用量记录',
})
const currentTitle = computed(() => (
  props.mode === 'ai'
    ? aiTitles[route.path] || '智能工作台'
    : route.meta.title || '管理后台'
))

function focusMenu() {
  menuButtonRef.value?.focus()
}

defineExpose({ focusMenu })
</script>

<style scoped lang="scss">
.platform-topbar { height: var(--platform-topbar-height); min-height: var(--platform-topbar-height); display: flex; align-items: center; gap: 12px; padding: 0 12px; background: var(--bg-container); border-bottom: 1px solid var(--gray-150); }
.platform-topbar button { font: inherit; }
.platform-topbar__menu { display: inline-flex; width: 32px; height: 32px; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.platform-topbar__menu:hover { border-color: var(--gray-150); background: var(--gray-50); color: var(--color-text); }
.platform-topbar__context { min-width: 0; display: grid; gap: 1px; }
.platform-topbar__context span { color: var(--color-text-tertiary); font-size: 11px; }
.platform-topbar__context strong { display: block; overflow: hidden; color: var(--color-text); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.platform-topbar__actions { min-width: 0; display: flex; align-items: center; gap: 6px; margin-left: auto; }
.platform-topbar__mode { min-height: 32px; display: inline-flex; align-items: center; gap: 8px; padding: 0 9px; border: 1px solid var(--gray-150); border-radius: var(--workspace-radius-sm); background: var(--bg-container); color: var(--color-text-secondary); cursor: pointer; }
.platform-topbar__mode:hover { background: var(--gray-50); color: var(--color-text); }
@media (max-width: 760px) {
  .platform-topbar { padding: 0 8px; }
  .platform-topbar__context { flex: 1 1 auto; }
  .platform-topbar__actions { flex: 0 0 auto; }
  .platform-topbar__context span { display: none; }
  .platform-topbar.has-workspace-return .platform-topbar__context { display: none; }
  .platform-topbar__mode { flex: 0 0 auto; justify-content: center; white-space: nowrap; }
}
</style>
