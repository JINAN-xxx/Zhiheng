<template>
  <div ref="scrollContainer" class="scroll-container" @scroll="emitScroll" @wheel="handleWheel">
    <slot />
  </div>
</template>

<script setup>
const emit = defineEmits(['scroll', 'updateArrows'])
const scrollContainer = ref()

function emitScroll() {
  emit('scroll')
  emit('updateArrows')
}

function handleWheel(event) {
  const container = scrollContainer.value
  if (!container || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
  event.preventDefault()
  container.scrollLeft += event.deltaY
}

function moveToTarget(currentTag) {
  const container = scrollContainer.value
  const target = container?.querySelector(`[data-path="${CSS.escape(currentTag.path)}"]`)
  target?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
}

function scrollToStart() {
  scrollContainer.value?.scrollTo({ left: 0, behavior: 'smooth' })
}

function scrollToEnd() {
  const container = scrollContainer.value
  container?.scrollTo({ left: container.scrollWidth, behavior: 'smooth' })
}

function getScrollState() {
  const container = scrollContainer.value
  if (!container) return { canLeft: false, canRight: false }
  return {
    canLeft: container.scrollLeft > 0,
    canRight: container.scrollLeft < container.scrollWidth - container.clientWidth - 1,
  }
}

defineExpose({ getScrollState, moveToTarget, scrollToEnd, scrollToStart })
</script>

<style scoped>
.scroll-container {
  width: 100%;
  height: 34px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  white-space: nowrap;
}

.scroll-container::-webkit-scrollbar {
  display: none;
}
</style>
