<template>
  <header class="workspace-toolbar">
    <div class="workspace-toolbar__leading">
      <button
        v-if="showMenuButton"
        type="button"
        class="workspace-toolbar__menu"
        aria-label="打开工作台导航"
        @click="emit('open-sidebar')"
      >
        <MenuOutlined />
      </button>
      <div>
        <h1 v-if="title" class="workspace-toolbar__title">{{ title }}</h1>
        <p v-if="subtitle" class="workspace-toolbar__subtitle">{{ subtitle }}</p>
      </div>
    </div>
    <div class="workspace-toolbar__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup>
import { MenuOutlined } from '@ant-design/icons-vue'

defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  showMenuButton: { type: Boolean, default: false },
})

const emit = defineEmits(['open-sidebar'])
</script>

<style scoped lang="scss">
.workspace-toolbar {
  display: flex;
  min-height: 64px;
  align-items: center;
  justify-content: space-between;
  gap: var(--workspace-space-4);
  padding: 0 var(--workspace-space-5);
  background: var(--workspace-surface);
  border-bottom: 1px solid var(--workspace-border);

  &__leading,
  &__actions {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: var(--workspace-space-3);
  }

  &__title {
    margin: 0;
    color: var(--workspace-text);
    font-size: 18px;
    line-height: 1.35;
  }

  &__subtitle {
    margin: 3px 0 0;
    color: var(--workspace-text-secondary);
    font-size: 12px;
  }

  &__menu {
    display: inline-flex;
    width: 32px;
    height: 32px;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--workspace-border);
    border-radius: var(--workspace-radius-sm);
    background: transparent;
    color: var(--workspace-text-secondary);
    cursor: pointer;
  }
}

@media (max-width: 767px) {
  .workspace-toolbar {
    padding: 0 var(--workspace-space-4);

    &__actions {
      gap: var(--workspace-space-2);
    }
  }
}
</style>
