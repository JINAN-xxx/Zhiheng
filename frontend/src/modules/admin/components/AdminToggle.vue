<script setup lang="ts">
import { Switch as ASwitch } from 'ant-design-vue'
defineProps<{ modelValue: boolean; label: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
function update(value: unknown) {
  if (typeof value === 'boolean') emit('update:modelValue', value)
}
</script>
<template>
  <div class="admin-toggle">
    <span>{{ label }}</span
    ><ASwitch
      :checked="modelValue"
      :disabled="disabled ?? false"
      :aria-label="label"
      @change="update"
    />
  </div>
</template>
<style scoped>
.admin-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 32px;
  font-size: 13px;
}
</style>
