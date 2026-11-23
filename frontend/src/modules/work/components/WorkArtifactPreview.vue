<script setup lang="ts">
import { computed } from 'vue'
import MessageFilePreview from '../../../shared/components/MessageFilePreview.vue'
import type { WorkArtifact } from '../api/index.ts'
import { artifactsByRun } from '../artifacts.ts'
import { useWorkArtifactFiles, workArtifactFile } from '../composables/artifactFiles.ts'

const props = defineProps<{
  taskId: string
  artifact: WorkArtifact | null
  artifacts: WorkArtifact[]
}>()
const emit = defineEmits<{ close: []; select: [artifact: WorkArtifact] }>()
const find = (id: string) => props.artifacts.find((item) => item.id === id)
const file = computed(() => (props.artifact ? workArtifactFile(props.artifact) : null))
const files = computed(() =>
  props.artifact
    ? (artifactsByRun(props.artifacts).get(props.artifact.runId) ?? []).map(workArtifactFile)
    : []
)
const { accessKey, readFile, readPreview } = useWorkArtifactFiles(() => props.taskId, find)
function select(id: string): void {
  const item = find(id)
  if (item) emit('select', item)
}
</script>

<template>
  <MessageFilePreview
    class="work-artifact-preview"
    :file="file"
    :files="files"
    :access-key="accessKey"
    :read-preview="readPreview"
    :read-file="readFile"
    @close="emit('close')"
    @select="select"
  />
</template>
