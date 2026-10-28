<!-- Adapted from the original RuoYi / 智衡 frontend, MIT licensed. -->
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
        <h1 v-if="title" class="workspace-toolbar__title" :title="title">{{ title }}</h1>
        <p v-if="subtitle" class="workspace-toolbar__subtitle">{{ subtitle }}</p>
      </div>
    </div>
    <div class="workspace-toolbar__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
import { MenuOutlined } from '@ant-design/icons-vue'

defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  showMenuButton: { type: Boolean, default: false }
})

const emit = defineEmits(['open-sidebar'])
</script>

<style scoped lang="scss">
.workspace-toolbar {
  display: flex;
  flex-shrink: 0;
  min-height: 56px;
  align-items: center;
  justify-content: space-between;
  gap: var(--workspace-space-4);
  padding: 0 24px;
  background: transparent;
  border-bottom: 0;

  &__leading,
  &__actions {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: var(--workspace-space-3);
  }

  &__leading {
    flex: 1 1 auto;
  }

  &__leading > div {
    min-width: 0;
  }

  &__actions {
    flex: 0 1 auto;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  &__title {
    display: -webkit-box;
    margin: 0;
    overflow: hidden;
    color: var(--color-text);
    font-size: var(--workspace-font-size-title);
    font-weight: var(--workspace-font-weight-medium);
    line-height: 1.35;
    overflow-wrap: anywhere;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
  }

  &__subtitle {
    margin: 3px 0 0;
    color: var(--color-text-secondary);
    font-size: var(--workspace-font-size-helper);
    line-height: var(--workspace-line-height);
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

@media (min-width: 992px) {
  .workspace-toolbar {
    min-height: 64px;
    padding: 8px var(--workspace-space-6);

    &__actions :deep(.ant-btn) {
      height: var(--workspace-control-height);
    }

    &__subtitle {
      font-size: 12px;
    }
  }
}

@media (max-width: 767px) {
  .workspace-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
    padding: 10px 14px;

    &__leading,
    &__actions {
      width: 100%;
    }

    &__actions {
      gap: var(--workspace-space-2);
      justify-content: flex-end;
    }
  }
}
</style>
