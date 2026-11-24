<script setup lang="ts">
import symbol from '../assets/daip-symbol.png'
import wordmark from '../assets/daip-wordmark.png'
import highResolutionWordmark from '../assets/daip-wordmark-large.png'

defineProps<{ compact?: boolean }>()
</script>

<template>
  <img v-if="compact" class="platform-wordmark" :src="symbol" alt="DAIP" width="32" height="32" />
  <img
    v-else
    class="platform-wordmark"
    :src="wordmark"
    :srcset="`${wordmark} 120w, ${highResolutionWordmark} 1200w`"
    sizes="120px"
    alt="DAIP"
    width="120"
    height="36"
  />
</template>

<style scoped>
.platform-wordmark {
  display: block;
  max-width: 100%;
  height: auto;
}
</style>
