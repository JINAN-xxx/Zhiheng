<script setup lang="ts">
defineOptions({ inheritAttrs: false })
</script>
<template>
  <div class="resource-card-grid-shell">
    <div v-bind="$attrs" class="resource-card-grid"><slot /></div>
  </div>
</template>
<style scoped>
.resource-card-grid-shell {
  width: 100%;
  min-width: 0;
  container: resource-cards / inline-size;
}
.resource-card-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  min-width: 0;
}
@container resource-cards (max-width: 1080px) {
  .resource-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@container resource-cards (max-width: 650px) {
  .resource-card-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 14px;
  }
}
</style>
