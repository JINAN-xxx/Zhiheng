<script setup lang="ts">
import { computed } from 'vue'
import MessageFileGrid from '../../../shared/components/MessageFileGrid.vue'
import type { WorkArtifact } from '../api/index.ts'
import { useWorkArtifactFiles, workArtifactFile } from '../composables/artifactFiles.ts'

const props = defineProps<{ taskId: string; artifacts: WorkArtifact[] }>()
const emit = defineEmits<{ preview: [artifact: WorkArtifact] }>()
const find = (id: string) => props.artifacts.find((item) => item.id === id)
const files = computed(() => props.artifacts.map(workArtifactFile))
const { accessKey, readFile, readPreview } = useWorkArtifactFiles(() => props.taskId, find)
function select(id: string): void {
  const item = find(id)
  if (item) emit('preview', item)
}
</script>

<template>
  <MessageFileGrid
    :files="files"
    :access-key="accessKey"
    :read-file="readFile"
    :read-preview="readPreview"
    label="本轮成果"
    id-prefix="work-artifact-"
    @preview="select"
  />
</template>
