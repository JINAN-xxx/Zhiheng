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
  min-height: 0;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--workspace-space-4);
  padding: 0;
  background: transparent;
  border-bottom: 0;

  &__leading,
  &__actions {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: var(--workspace-space-3);
  }

  &__title {
    margin: 0;
    color: var(--color-text);
    font-size: 22px;
    font-weight: 600;
    line-height: 1.35;
  }

  &__subtitle {
    margin: 3px 0 0;
    color: var(--color-text-secondary);
    font-size: 14px;
  }

  &__menu {
    display: inline-flex;
    width: 32px;
    height: 32px;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--gray-150);
    border-radius: var(--workspace-radius-sm);
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;

    &:hover {
      background: var(--gray-10);
      color: var(--color-text);
    }

    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: 2px;
    }
  }
}

@media (max-width: 767px) {
  .workspace-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
    padding: 0;

    &__actions {
      width: 100%;
      gap: var(--workspace-space-2);
      justify-content: flex-end;
    }
  }
}
</style>
