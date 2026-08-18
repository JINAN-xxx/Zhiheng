<template>
  <div class="workspace-shell" :class="{ 'is-mobile': isMobile, 'is-collapsed': sidebarCollapsed }">
    <div v-if="isMobile && sidebarOpen" class="workspace-shell__scrim" @click="closeSidebar" />
    <aside
      class="workspace-shell__sidebar"
      :class="{ 'is-open': sidebarOpen }"
      :aria-hidden="isMobile && !sidebarOpen"
    >
      <slot name="sidebar" :close-sidebar="closeSidebar" />
    </aside>
    <section class="workspace-shell__main">
      <slot name="toolbar" :open-sidebar="openSidebar" />
      <div class="workspace-shell__content">
        <slot />
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  sidebarCollapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['open-sidebar', 'close-sidebar'])
const viewportWidth = ref(typeof window === 'undefined' ? 1440 : window.innerWidth)
const sidebarOpen = ref(false)
const isMobile = computed(() => viewportWidth.value < 768)
const sidebarCollapsed = computed(() => props.sidebarCollapsed && !isMobile.value)

function handleResize() {
  viewportWidth.value = window.innerWidth
  if (!isMobile.value) sidebarOpen.value = false
}

function openSidebar() {
  sidebarOpen.value = true
  emit('open-sidebar')
}

function closeSidebar() {
  sidebarOpen.value = false
  emit('close-sidebar')
}

onMounted(() => window.addEventListener('resize', handleResize, { passive: true }))
onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
</script>

<style scoped lang="scss">
.workspace-shell {
  display: flex;
  min-height: 100%;
  background: var(--workspace-bg);

  &__sidebar {
    width: var(--workspace-sidebar-width);
    flex: 0 0 var(--workspace-sidebar-width);
    min-height: 100%;
    background: var(--workspace-surface);
    border-right: 1px solid var(--workspace-border);
    transition: width 0.2s ease, transform 0.2s ease;
    z-index: 20;
  }

  &.is-collapsed &__sidebar {
    width: var(--workspace-sidebar-collapsed-width);
    flex-basis: var(--workspace-sidebar-collapsed-width);
  }

  &__main {
    display: flex;
    flex: 1;
    min-width: 0;
    min-height: 100%;
    flex-direction: column;
  }

  &__content {
    flex: 1;
    min-width: 0;
    padding: var(--workspace-space-5);
  }

  &__scrim {
    position: fixed;
    inset: 0;
    z-index: 19;
    background: rgba(21, 22, 22, 0.36);
  }
}

@media (max-width: 767px) {
  .workspace-shell {
    &__sidebar {
      position: fixed;
      inset: 0 auto 0 0;
      width: min(288px, calc(100vw - 48px));
      flex-basis: auto;
      transform: translateX(-100%);
      box-shadow: var(--workspace-shadow-float);
    }

    &__sidebar.is-open {
      transform: translateX(0);
    }

    &__content {
      padding: var(--workspace-space-4);
    }
  }
}
</style>
