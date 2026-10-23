<!-- Adapted from original ResourceDrawer, MIT licensed. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { Drawer as ADrawer } from 'ant-design-vue'
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    width?: number | string
    destroyOnClose?: boolean
  }>(),
  { width: 640, destroyOnClose: false }
)
const emit = defineEmits<{ close: [] }>()
const { width: viewportWidth } = useWindowSize()
const drawerWidth = computed(() =>
  viewportWidth.value < 768
    ? '100%'
    : typeof props.width === 'number'
      ? Math.min(props.width, viewportWidth.value)
      : props.width
)
</script>
<template>
  <ADrawer
    :destroy-on-close="destroyOnClose"
    :open="open"
    :title="title"
    :width="drawerWidth"
    @close="emit('close')"
  >
    <slot />
    <template v-if="$slots.footer" #footer
      ><div class="antd-resource-drawer__footer"><slot name="footer" /></div
    ></template>
  </ADrawer>
</template>
<style scoped>
.antd-resource-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
