<script setup lang="ts">
import { computed, ref } from 'vue'
import { downloadAssistantAttachment } from '../api/conversations.ts'
import type { AssistantAttachment } from '../api/conversations.ts'
import { identity } from '../../identity/public.ts'
import { attachmentFile, attachmentPreview } from '../../../shared/messageFiles.ts'
import MessageFileGrid from '../../../shared/components/MessageFileGrid.vue'
import MessageFilePreview from '../../../shared/components/MessageFilePreview.vue'
import AttachmentParseWarnings from '../../../shared/components/AttachmentParseWarnings.vue'

const props = defineProps<{
  agentId: string
  conversationId: string | null
  files: AssistantAttachment[]
  disabled?: boolean
}>()
const selectedId = ref('')
const items = computed(() => props.files.map(attachmentFile))
const selected = computed(() => items.value.find((file) => file.id === selectedId.value) ?? null)
const accessKey = computed(() => {
  const owner = identity.value?.id
  return !props.disabled && props.conversationId && owner
    ? `${owner}:${props.agentId}:${props.conversationId}`
    : null
})
function readFile(id: string, signal: AbortSignal): Promise<Blob> {
  if (!accessKey.value || !props.conversationId) throw new Error('当前会话附件不可用')
  return downloadAssistantAttachment(props.agentId, props.conversationId, id, signal)
}
async function readPreview(id: string, signal: AbortSignal) {
  const file = items.value.find((item) => item.id === id)
  return file ? attachmentPreview(file, readFile, signal) : null
}
</script>

<template>
  <div v-if="files.length" class="assistant-files">
    <AttachmentParseWarnings :files="files" />
    <MessageFileGrid
      :files="items"
      :access-key="accessKey"
      :read-file="readFile"
      :read-preview="readPreview"
      label="消息附件"
      @preview="selectedId = $event"
    />
    <MessageFilePreview
      v-if="selected"
      :file="selected"
      :files="items"
      :access-key="accessKey"
      :read-file="readFile"
      :read-preview="readPreview"
      @close="selectedId = ''"
      @select="selectedId = $event"
    />
  </div>
</template>

<style scoped>
.assistant-files {
  width: min(520px, 100%);
}
</style>
