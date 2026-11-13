<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { AiMessage } from '../../chat/public.ts'

const props = withDefaults(defineProps<{ text: string; animate?: boolean }>(), { animate: true })
const visible = ref('')
const typing = ref(false)
let frame: number | null = null
function stop(): void {
  if (frame !== null) cancelAnimationFrame(frame)
  frame = null
}
watch(
  () => [props.text, props.animate] as const,
  ([text, animate]) => {
    stop()
    visible.value = ''
    typing.value = false
    if (!text || !animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      visible.value = text
      return
    }
    const characters = Array.from(text)
    const speed = Math.max(45, characters.length / 6)
    const started = performance.now()
    typing.value = true
    function advance(now: number): void {
      const count = Math.min(
        characters.length,
        Math.max(1, Math.floor(((now - started) * speed) / 1000))
      )
      visible.value = characters.slice(0, count).join('')
      if (count < characters.length) frame = requestAnimationFrame(advance)
      else {
        frame = null
        typing.value = false
      }
    }
    frame = requestAnimationFrame(advance)
  },
  { immediate: true }
)
onBeforeUnmount(stop)
</script>

<template>
  <div class="assistant-welcome"><AiMessage :content="visible" :loading="typing" /></div>
</template>
