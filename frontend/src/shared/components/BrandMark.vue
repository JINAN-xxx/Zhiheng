<script setup lang="ts">
defineProps<{ compact?: boolean }>()
</script>
<template>
  <span class="platform-wordmark">
    <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M8 7h17L8 24h16M4 16h9" /></svg>
    <span v-if="!compact">智衡</span>
  </span>
</template>
<style scoped>
.platform-wordmark {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
  color: var(--color-text);
  font-size: 20px;
  font-weight: 600;
}
.platform-wordmark svg {
  width: 30px;
  height: 32px;
  fill: none;
  stroke: var(--main-color);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
