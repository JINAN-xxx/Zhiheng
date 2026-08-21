<template>
  <div class="workspace-sidebar">
    <div class="workspace-sidebar__brand">
      <slot name="brand">
        <span class="workspace-sidebar__mark" aria-hidden="true">智</span>
        <span v-if="!collapsed" class="workspace-sidebar__name">智衡 AI</span>
      </slot>
    </div>
    <nav class="workspace-sidebar__nav" aria-label="工作台导航">
      <router-link
        v-for="item in items"
        :key="item.key"
        class="workspace-sidebar__item"
        :class="{ 'is-active': item.active }"
        :to="item.to"
        :title="collapsed ? item.label : undefined"
        @click="emit('select', item)"
      >
        <span class="workspace-sidebar__icon" aria-hidden="true"><component :is="item.icon" /></span>
        <span v-if="!collapsed" class="workspace-sidebar__label">{{ item.label }}</span>
      </router-link>
    </nav>
    <div class="workspace-sidebar__bottom">
      <slot name="bottom" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  collapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])
</script>

<style scoped lang="scss">
.workspace-sidebar {
  display: flex;
  min-height: 100%;
  flex-direction: column;
  padding: var(--workspace-space-4) var(--workspace-space-3);

  &__brand {
    display: flex;
    align-items: center;
    min-height: 40px;
    gap: var(--workspace-space-2);
    padding: 0 var(--workspace-space-2) var(--workspace-space-4);
    color: var(--workspace-text);
    font-weight: 700;
  }

  &__mark {
    display: inline-flex;
    width: 30px;
    height: 30px;
    align-items: center;
    justify-content: center;
    border-radius: var(--workspace-radius-sm);
    background: var(--workspace-main);
    color: #ffffff;
  }

  &__name {
    white-space: nowrap;
  }

  &__nav {
    display: grid;
    gap: var(--workspace-space-1);
  }

  &__item {
    display: flex;
    min-height: 40px;
    align-items: center;
    gap: var(--workspace-space-3);
    padding: 0 var(--workspace-space-2);
    border-radius: var(--workspace-radius-sm);
    color: var(--workspace-text-secondary);
    transition: background 0.16s ease, color 0.16s ease;

    &:hover,
    &.is-active {
      background: var(--workspace-main-soft);
      color: var(--workspace-main-deep);
    }
  }

  &__label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__icon {
    display: inline-flex;
    width: 18px;
    flex: 0 0 18px;
    align-items: center;
    justify-content: center;
    font-size: 16px;
  }

  &__bottom {
    margin-top: auto;
    padding-top: var(--workspace-space-4);
  }
}
</style>
