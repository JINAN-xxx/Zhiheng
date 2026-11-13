<script setup lang="ts">
import { Button as AButton, Tooltip as ATooltip } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { nextTick } from 'vue'

defineOptions({ inheritAttrs: false })
const props = withDefaults(
  defineProps<{ label: string; loading?: boolean; disabled?: boolean }>(),
  { loading: false, disabled: false }
)
const emit = defineEmits<{ click: [event: MouseEvent] }>()
let dispatching = false
function refresh(event: MouseEvent): void {
  if (props.loading || props.disabled || dispatching) return
  dispatching = true
  emit('click', event)
  void nextTick(() => {
    dispatching = false
  })
}
</script>

<template>
  <ATooltip :title="label" placement="bottom">
    <AButton
      v-bind="{ ...$attrs, 'aria-busy': loading }"
      class="workspace-refresh-button"
      type="default"
      :aria-label="label"
      :loading="loading"
      :disabled="disabled || loading"
      @click="refresh"
    >
      <template #icon><ReloadOutlined /></template>
    </AButton>
  </ATooltip>
</template>

<style scoped>
.workspace-refresh-button.ant-btn.ant-btn-default {
  box-sizing: border-box;
  display: inline-flex;
  width: 36px;
  min-width: 36px;
  max-width: 36px;
  height: 36px;
  min-height: 36px;
  max-height: 36px;
  flex: 0 0 36px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  box-shadow: none;
  font-size: 16px;
}
.workspace-refresh-button:not(:disabled):hover {
  border-color: var(--main-300);
  background: var(--gray-25);
  color: var(--main-color);
}
.workspace-refresh-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.workspace-refresh-button.ant-btn:disabled {
  color: var(--color-text-tertiary);
}
</style>
