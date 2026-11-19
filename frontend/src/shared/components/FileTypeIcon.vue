<script setup lang="ts">
import { computed } from 'vue'
import { FileTextOutlined, PictureOutlined } from '@ant-design/icons-vue'

const props = withDefaults(defineProps<{ filename: string; mime?: string }>(), { mime: '' })
const image = computed(
  () =>
    props.mime.startsWith('image/') ||
    /\.(png|jpe?g|gif|webp|bmp|svg|avif|tiff?)$/i.test(props.filename)
)
</script>

<template>
  <span class="attachment-file-icon" aria-hidden="true"
    ><PictureOutlined v-if="image" /><FileTextOutlined v-else
  /></span>
</template>

<style scoped>
.attachment-file-icon {
  display: inline-flex;
  flex: 0 0 16px;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--color-text-secondary);
  font-size: 16px;
  line-height: 1;
  vertical-align: middle;
}
</style>
