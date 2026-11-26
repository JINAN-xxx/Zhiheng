import { computed, onBeforeUnmount, ref } from 'vue'
import type { Ref } from 'vue'
import { feedback as toast } from '../../../shared/feedback.ts'
import { canUseCapability } from '../../identity/public.ts'
import type { ChatModel } from '../../models/public.ts'
import type { Skill } from '../../skills/public.ts'
import { uploadChatAttachment } from '../api/index.ts'
import type { ChatMessage } from '../api/index.ts'
import { createChatFileQueue } from '../attachments.ts'
import type { QueuedFile } from '../attachments.ts'
import { defaultGenerationSettings, generationSettingsError } from '../generationSettings.ts'
import type { ChatGenerationSettings } from '../generationSettings.ts'

interface ComposerOptions {
  readOnly: Ref<boolean>
  loading: Ref<boolean>
  error: Ref<string>
  isSending: () => boolean
  isRunning: () => boolean
}

export function useChatComposer(options: ComposerOptions) {
  const { readOnly, loading, error } = options
  const sending = computed(options.isSending),
    running = computed(options.isRunning)
  const models = ref<ChatModel[]>([])

  const modelId = ref<string>()

  let modelNeedsSelection = false

  const generationSettings = ref<ChatGenerationSettings>(defaultGenerationSettings())

  const selectedModel = computed(
    () => models.value.find((model) => model.id === modelId.value) ?? null
  )

  const draft = ref('')

  const queuedFiles = ref<QueuedFile[]>([])

  const queue = createChatFileQueue({
    upload: uploadChatAttachment,
    onChange: (files) => (queuedFiles.value = files)
  })

  const supportsVision = computed(
    () => models.value.find((model) => model.id === modelId.value)?.vision_enabled === true
  )

  const fileQueueReady = computed(() =>
    queuedFiles.value.every((file) => file.status === 'ready' && file.fileId)
  )

  function addFiles(files: Iterable<File>): void {
    if (!canUseCapability('chat', 'use') || sending.value || running.value || readOnly.value) return
    const result = queue.add(files, supportsVision.value)
    if (result.rejected.length)
      void toast.warning({
        key: 'chat-file-validation',
        content: [...new Set(result.rejected.map((item) => item.error))].join('；'),
        duration: 3
      })
  }

  function removeFile(id: string): void {
    if (!sending.value && !running.value) queue.remove(id)
  }

  function retryFile(id: string): void {
    if (canUseCapability('chat', 'use') && !sending.value && !running.value && !readOnly.value)
      void queue.retry(id)
  }

  const selectedSkills = ref<Skill[]>([])

  const skillPickerOpen = ref(false)

  const modelOptions = computed(() =>
    models.value
      .filter((model) => model.enabled && model.capabilities.includes('chat'))
      .map((model) => ({ value: model.id, label: model.name }))
  )

  function applyGenerationSettings(settings: ChatGenerationSettings): void {
    if (
      !canUseCapability('chat', 'use') ||
      sending.value ||
      running.value ||
      loading.value ||
      readOnly.value
    )
      return
    const settingsError = generationSettingsError(settings, selectedModel.value?.max_output_tokens)
    if (settingsError) {
      error.value = settingsError
      return
    }
    generationSettings.value = { ...settings }
  }

  function setModelId(value: unknown): void {
    modelId.value = typeof value === 'string' ? value : undefined
    if (modelId.value) modelNeedsSelection = false
    if (modelId.value && error.value === '原模型当前不可用，请重新选择模型') error.value = ''
  }

  function removeSkill(slug: string): void {
    if (!sending.value && !running.value)
      selectedSkills.value = selectedSkills.value.filter((skill) => skill.skillSlug !== slug)
  }

  function clear(): void {
    queue.clear()
    draft.value = ''
    selectedSkills.value = []
    skillPickerOpen.value = false
  }
  function prepareSelection(): void {
    if (modelNeedsSelection) modelId.value = modelOptions.value[0]?.value
    modelNeedsSelection = false
    generationSettings.value = defaultGenerationSettings()
  }
  function ensureModelSelection(): void {
    if (!modelNeedsSelection && !modelOptions.value.some((model) => model.value === modelId.value))
      modelId.value = modelOptions.value[0]?.value
  }
  function restoreGeneration(config: NonNullable<ChatMessage['generation_config']>): void {
    generationSettings.value = {
      system_prompt: config.system_prompt,
      temperature: config.temperature,
      max_output_tokens: config.max_output_tokens
    }
    if (modelOptions.value.some((model) => model.value === config.model_id))
      modelId.value = config.model_id
    else {
      modelId.value = undefined
      modelNeedsSelection = true
      error.value = '原模型当前不可用，请重新选择模型'
    }
  }
  onBeforeUnmount(() => {
    clear()
    generationSettings.value = defaultGenerationSettings()
  })
  return {
    models,
    modelId,
    selectedModel,
    modelOptions,
    generationSettings,
    draft,
    queuedFiles,
    supportsVision,
    fileQueueReady,
    selectedSkills,
    skillPickerOpen,
    addFiles,
    removeFile,
    removeSubmittedFiles: (ids: string[]) => ids.forEach((id) => queue.remove(id)),
    retryFile,
    applyGenerationSettings,
    setModelId,
    removeSkill,
    clear,
    prepareSelection,
    ensureModelSelection,
    restoreGeneration
  }
}
