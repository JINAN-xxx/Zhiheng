<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Empty as AEmpty,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Tabs as ATabs,
  TabPane as ATabPane,
  Tag as ATag
} from 'ant-design-vue'
import { DownloadOutlined, EyeOutlined, HistoryOutlined } from '@ant-design/icons-vue'
import { formatFileSize, saveBlob } from '../../files/public.ts'
import { identity, canUseModule } from '../../identity/public.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { downloadArtifact, downloadInput } from '../api/index.ts'
import type { WorkArtifact, WorkInput } from '../api/index.ts'
import AttachmentButton from '../../../shared/components/AttachmentButton.vue'
import FileTypeIcon from '../../../shared/components/FileTypeIcon.vue'

const props = defineProps<{
  taskId: string
  inputs: WorkInput[]
  artifacts: WorkArtifact[]
  uploading: boolean
  canUpload: boolean
}>()
const emit = defineEmits<{ upload: [files: File[]] }>()
const tab = ref(props.artifacts.length ? 'artifacts' : 'inputs'),
  fileInput = ref<HTMLInputElement | null>(null),
  error = ref(''),
  pending = ref('')
const previewOpen = ref(false),
  previewName = ref(''),
  previewUrl = ref(''),
  previewText = ref(''),
  previewKind = ref('')
const scope = new RequestScope()
type FileVersion =
  | (WorkInput & { kind: 'input'; id: string })
  | (WorkArtifact & { kind: 'artifact' })
function group<T extends { name: string; version: number }>(
  items: T[],
  key: (item: T) => string = (item) => item.name
): { key: string; name: string; latest: T; versions: T[] }[] {
  const groups = new Map<string, T[]>()
  for (const item of items) groups.set(key(item), [...(groups.get(key(item)) ?? []), item])
  return [...groups].map(([identity, versions]) => {
    versions.sort((a, b) => b.version - a.version)
    return { key: identity, name: versions[0]!.name, latest: versions[0]!, versions }
  })
}
const inputGroups = computed(() =>
  group(props.inputs.map((item): FileVersion => ({ ...item, kind: 'input', id: item.fileId })))
)
const artifactGroups = computed(() =>
  group(
    props.artifacts.map((item): FileVersion => ({ ...item, kind: 'artifact' })),
    (item) => (item.kind === 'artifact' ? item.logicalPath : item.name)
  )
)
const groups = computed(() => (tab.value === 'inputs' ? inputGroups.value : artifactGroups.value))
watch(
  () => props.artifacts.length,
  (count, previous) => {
    if (count && !previous) tab.value = 'artifacts'
  }
)
function pick(event: Event): void {
  const input = event.target as HTMLInputElement,
    files = [...(input.files ?? [])]
  input.value = ''
  if (files.length && props.canUpload && !props.uploading) emit('upload', files)
}
function canPreview(item: FileVersion): boolean {
  return (
    item.kind === 'artifact' &&
    (!!item.previewFileId ||
      item.mime === 'application/pdf' ||
      /^image\/(png|jpeg|webp|gif)$/.test(item.mime) ||
      /\.(txt|md|csv|json)$/i.test(item.name))
  )
}
function closePreview(): void {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  previewText.value = ''
  previewOpen.value = false
  previewKind.value = ''
}
async function open(item: FileVersion, preview = false): Promise<void> {
  const ticket = scope.replace(),
    taskId = props.taskId,
    ownerId = identity.value?.id
  pending.value = item.id
  error.value = ''
  try {
    const blob =
      item.kind === 'input'
        ? await downloadInput(taskId, item.fileId, ticket.signal)
        : await downloadArtifact(taskId, item.id, ticket.signal, preview)
    if (
      !ticket.isCurrent() ||
      props.taskId !== taskId ||
      identity.value?.id !== ownerId ||
      !canUseModule('work')
    )
      return
    if (!preview) {
      saveBlob(blob, item.name)
      return
    }
    closePreview()
    previewName.value = `${item.name} · v${item.version}`
    const mime = blob.type.split(';')[0] || item.mime
    if (mime === 'application/pdf') {
      previewKind.value = 'pdf'
      previewUrl.value = URL.createObjectURL(blob)
    } else if (/^image\/(png|jpeg|webp|gif)$/.test(mime)) {
      previewKind.value = 'image'
      previewUrl.value = URL.createObjectURL(blob)
    } else if (mime.startsWith('text/') || /\.(txt|md|csv|json)$/i.test(item.name)) {
      const text = await blob.text()
      if (!ticket.isCurrent() || identity.value?.id !== ownerId) return
      previewKind.value = 'text'
      previewText.value = text.slice(0, 200000)
    } else throw new Error('此格式暂不支持预览，请下载查看')
    previewOpen.value = true
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) pending.value = ''
  }
}
watch([() => props.taskId, () => identity.value?.id, () => canUseModule('work')], () => {
  scope.replace()
  closePreview()
  error.value = ''
  pending.value = ''
})
onBeforeUnmount(() => {
  scope.dispose()
  closePreview()
})
</script>
<template>
  <section class="work-files" aria-label="任务资料与成果">
    <div class="work-files__heading">
      <strong>资料与成果</strong
      ><AttachmentButton
        v-if="canUpload"
        label="上传"
        aria-label="上传任务资料"
        :loading="uploading"
        @click="fileInput?.click()"
      />
    </div>
    <AAlert v-if="error" :message="error" type="error" show-icon closable @close="error = ''" />
    <ATabs :active-key="tab" size="small" @update:active-key="tab = String($event)"
      ><ATabPane key="inputs" :tab="`资料 ${inputGroups.length}`" /><ATabPane
        key="artifacts"
        :tab="`成果 ${artifactGroups.length}`"
    /></ATabs>
    <div class="work-files__list workspace-scroll-area">
      <AEmpty
        v-if="!groups.length"
        :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        :description="tab === 'inputs' ? '上传需要处理的资料' : '生成的成果会显示在这里'"
      />
      <article v-for="entry in groups" :key="entry.key" class="work-file">
        <div class="work-file__title">
          <FileTypeIcon :filename="entry.name" :mime="entry.latest.mime" /><strong
            :title="entry.name"
            >{{ entry.name }}</strong
          ><ATag>v{{ entry.latest.version }}</ATag>
        </div>
        <p class="work-file__size">{{ formatFileSize(entry.latest.size) }}</p>
        <p
          v-if="entry.latest.kind === 'artifact'"
          class="work-file__validation"
          :class="{ 'is-error': entry.latest.validation.valid === false }"
        >
          {{
            entry.latest.validation.valid === true
              ? '结构检查通过'
              : entry.latest.validation.valid === false
                ? '结构检查未通过'
                : '成果已登记，请核对内容'
          }}
        </p>
        <div class="work-file__actions">
          <AButton
            v-if="canPreview(entry.latest)"
            size="small"
            type="text"
            aria-label="预览"
            :loading="pending === entry.latest.id"
            @click="open(entry.latest, true)"
            ><EyeOutlined />预览</AButton
          ><AButton
            size="small"
            type="text"
            aria-label="下载"
            :loading="pending === entry.latest.id"
            @click="open(entry.latest)"
            ><DownloadOutlined />下载</AButton
          ><ADropdown v-if="entry.versions.length > 1" :trigger="['click']"
            ><AButton size="small" type="text" aria-label="查看文件版本"
              ><HistoryOutlined />版本</AButton
            ><template #overlay
              ><AMenu
                ><AMenuItem
                  v-for="version in entry.versions"
                  :key="version.id"
                  @click="open(version)"
                  >v{{ version.version }} · {{ formatFileSize(version.size)
                  }}<span v-if="version.id === entry.latest.id"> · 最新</span></AMenuItem
                ></AMenu
              ></template
            ></ADropdown
          >
        </div>
      </article>
    </div>
    <input
      ref="fileInput"
      type="file"
      multiple
      class="work-files__input"
      aria-label="选择任务资料"
      @change="pick"
    />
    <AModal
      :open="previewOpen"
      :title="previewName"
      :footer="null"
      :width="960"
      :style="{ maxWidth: 'calc(100vw - 32px)' }"
      @cancel="closePreview"
      ><img
        v-if="previewKind === 'image'"
        class="work-files__image"
        :src="previewUrl"
        :alt="previewName"
      /><iframe
        v-else-if="previewKind === 'pdf'"
        class="work-files__pdf"
        :src="previewUrl"
        :title="previewName"
      />
      <pre v-else-if="previewKind === 'text'" class="work-files__text">{{ previewText }}</pre>
    </AModal>
  </section>
</template>
<style scoped>
.work-files {
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 20px 16px;
  background: var(--bg-container);
  color: var(--color-text);
}
.work-files__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 28px;
}
.work-files__heading > strong {
  font-size: 14px;
  font-weight: 500;
}
.work-files > :deep(.ant-alert) {
  margin: 10px 0 0;
}
.work-files__list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.work-file {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
}
.work-file__title {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.work-file__title strong {
  min-width: 0;
  flex: 1;
  overflow-wrap: anywhere;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.6;
}
.work-file__title :deep(.ant-tag) {
  margin: 0;
  font-size: 11px;
  border-color: var(--gray-150);
}
.work-file__size,
.work-file__validation {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.work-file__validation.is-error {
  color: var(--workspace-danger);
}
.work-file__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin: 8px -7px -3px;
}
.work-file__actions :deep(.ant-btn) {
  padding-inline: 7px;
  color: var(--color-text-secondary);
}
.work-files__input {
  display: none;
}
.work-files__image {
  display: block;
  max-width: 100%;
  max-height: 70vh;
  margin: auto;
  object-fit: contain;
}
.work-files__pdf {
  width: 100%;
  height: 70vh;
  border: 0;
}
.work-files__text {
  max-height: 70vh;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.8;
}
</style>
