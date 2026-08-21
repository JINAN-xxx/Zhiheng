<template>
  <slot />
</template>

<script setup>
import { onUnmounted } from 'vue'
import { App } from 'ant-design-vue'

import { registerFeedbackApis } from '@/plugins/antdFeedback'

const unregister = registerFeedbackApis(App.useApp())

onUnmounted(unregister)
</script>
