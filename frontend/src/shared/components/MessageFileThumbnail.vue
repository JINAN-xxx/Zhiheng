<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useIntersectionObserver } from '@vueuse/core'
import { PictureOutlined } from '@ant-design/icons-vue'
import { useMessageFileRequest } from '../composables/useMessageFileRequest.ts'
import type { MessageFile, ReadFilePreview } from '../messageFiles.ts'

const props = defineProps<{
  file: MessageFile
  accessKey: string | null
  preview: ReadFilePreview
}>()
const root = ref<HTMLElement | null>(null),
  visible = ref(false),
  url = ref('')
const { read, error } = useMessageFileRequest(() => props.accessKey)
useIntersectionObserver(
  root,
  ([entry]) => {
    visible.value = !!entry?.isIntersecting
  },
  { rootMargin: '160px' }
)
function release(): void {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
}
watch(() => props.accessKey, release, { flush: 'sync' })
watch(
  [visible, () => props.accessKey],
  async () => {
    if (!visible.value || url.value) return
    const value = await read(props.file.id, (signal) => props.preview(props.file.id, signal))
    if (value?.kind === 'image') url.value = URL.createObjectURL(value.blob)
  },
  { immediate: true }
)
onBeforeUnmount(release)
</script>

<template>
  <span ref="root" class="artifact-image">
    <img v-if="url" :src="url" :alt="file.name" decoding="async" />
    <span v-else class="artifact-image__placeholder">
      <PictureOutlined />
      <span>{{ error ? '图片加载失败' : accessKey ? '加载图片…' : '' }}</span>
    </span>
  </span>
</template>

<style scoped>
.artifact-image {
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 4 / 3;
  background: var(--gray-25, #fafafa);
  overflow: hidden;
}
.artifact-image img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.artifact-image__placeholder {
  display: grid;
  justify-items: center;
  gap: 8px;
  padding: 16px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.artifact-image__placeholder > :first-child {
  font-size: 24px;
}
</style>
