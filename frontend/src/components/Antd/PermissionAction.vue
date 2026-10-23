<template>
  <slot v-if="visible" :disabled="disabled" :run="run" />
</template>

<script setup>
import { computed } from 'vue'

import useUserStore from '@/store/modules/user'
import { canRunAction } from './componentContracts'

const props = defineProps({
  permissions: { type: Array, default: () => [] },
  blocked: { type: Boolean, default: false },
  hideWhenDenied: { type: Boolean, default: true },
})

const emit = defineEmits(['run', 'denied'])
const userStore = useUserStore()
const permitted = computed(() => canRunAction(userStore.permissions, props.permissions, props.blocked))
const visible = computed(() => permitted.value || !props.hideWhenDenied)
const disabled = computed(() => !permitted.value)

function run(...args) {
  if (!permitted.value) {
    emit('denied')
    return false
  }
  emit('run', ...args)
  return true
}
</script>
