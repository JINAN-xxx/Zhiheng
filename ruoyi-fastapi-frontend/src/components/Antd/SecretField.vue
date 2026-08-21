<template>
  <a-textarea
    v-if="multiline"
    autocomplete="new-password"
    :auto-size="{ minRows: 4, maxRows: 10 }"
    :placeholder="placeholder"
    :value="value"
    @update:value="updateValue"
  />
  <a-input-password
    v-else
    autocomplete="new-password"
    :placeholder="placeholder"
    :value="value"
    @update:value="updateValue"
  />
</template>

<script setup>
defineProps({
  value: { type: String, default: '' },
  multiline: { type: Boolean, default: false },
  placeholder: { type: String, default: '输入秘密值' },
})

const emit = defineEmits(['update:value'])
function updateValue(value) {
  emit('update:value', value)
}
</script>
