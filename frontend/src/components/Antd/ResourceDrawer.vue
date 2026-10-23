<template>
  <a-drawer
    :destroy-on-close="destroyOnClose"
    :open="open"
    :title="title"
    :width="drawerWidth"
    @close="emit('close')"
  >
    <slot />
    <template v-if="$slots.footer" #footer>
      <div class="antd-resource-drawer__footer">
        <slot name="footer" />
      </div>
    </template>
  </a-drawer>
</template>

<script setup>
import { computed } from 'vue'
import { useWindowSize } from '@vueuse/core'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  width: { type: [Number, String], default: 640 },
  destroyOnClose: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])
const { width: viewportWidth } = useWindowSize()
const drawerWidth = computed(() => viewportWidth.value < 768 ? '100%' : props.width)
</script>

<style scoped>
.antd-resource-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
