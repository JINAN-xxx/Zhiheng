import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createModel, updateModel } from '../api/index.ts'
import type { ChatModel, ConnectionOption } from '../api/index.ts'
import type { ModelAccess } from './useModelAccess.ts'

interface ModelConfigurationOptions {
  access: ModelAccess
  connections: () => ConnectionOption[]
  pending: Record<string, boolean>
  saved: (model: ChatModel, editing: boolean) => void
}
export function useModelConfiguration(options: ModelConfigurationOptions) {
  const { canCreateModels, canUpdateModels } = options.access
  const modelOpen = ref(false)
  const editingModel = ref<ChatModel | null>(null)
  const saving = ref(false)
  const modalError = ref('')
  const mutationScope = new RequestScope()
  const modelForm = reactive<{
    name: string
    connection_id: string
    upstream_model: string
    max_output_tokens: number | null
    context_window_tokens: number | null
    capacity_note: string
    model_type: ChatModel['model_type']
    vision_enabled: boolean
  }>({
    name: '',
    connection_id: '',
    upstream_model: '',
    max_output_tokens: 512,
    context_window_tokens: null,
    capacity_note: '',
    model_type: 'chat',
    vision_enabled: false
  })
  function openModel(model: ChatModel | null = null): void {
    if (
      !(model ? canUpdateModels.value : canCreateModels.value) ||
      (model && options.pending[model.id])
    )
      return
    mutationScope.replace()
    saving.value = false
    editingModel.value = model
    Object.assign(modelForm, {
      name: model?.name ?? '',
      connection_id:
        model?.connection_id ??
        (options.connections().filter((item) => item.enabled).length === 1
          ? (options.connections().find((item) => item.enabled)?.id ?? '')
          : ''),
      upstream_model: model?.upstream_model ?? '',
      max_output_tokens: model?.max_output_tokens ?? 512,
      context_window_tokens: model?.context_window_tokens ?? null,
      capacity_note: model?.capacity_note ?? '',
      model_type: model?.model_type ?? 'chat',
      vision_enabled: model?.vision_enabled ?? false
    })
    modelOpen.value = true
    modalError.value = ''
  }
  function closeModel(): void {
    modelOpen.value = false
    mutationScope.replace()
    saving.value = false
    modalError.value = ''
  }
  function setConnectionId(value: unknown): void {
    modelForm.connection_id = typeof value === 'string' ? value : ''
  }
  function setMaxOutputTokens(value: unknown): void {
    modelForm.max_output_tokens = typeof value === 'number' ? value : null
  }
  function setModelType(value: unknown): void {
    if (value === 'chat' || value === 'embedding' || value === 'rerank')
      modelForm.model_type = value
  }
  async function saveModel(): Promise<void> {
    if (saving.value || !(editingModel.value ? canUpdateModels.value : canCreateModels.value))
      return
    if (!modelForm.upstream_model.trim()) {
      modalError.value = '请填写上游模型标识'
      return
    }
    if (!modelForm.name.trim()) {
      modalError.value = '请填写模型显示名称'
      return
    }
    if (!modelForm.connection_id) {
      modalError.value = '请选择模型连接'
      return
    }
    const maxOutputTokens = modelForm.max_output_tokens
    if (maxOutputTokens === null || !Number.isSafeInteger(maxOutputTokens) || maxOutputTokens < 1) {
      modalError.value = '输出上限须为正整数'
      return
    }
    const windowTokens = modelForm.context_window_tokens
    if (
      windowTokens !== null &&
      (!Number.isInteger(windowTokens) || windowTokens < 256 || windowTokens > 2097152)
    ) {
      modalError.value = '上下文窗口须为 256–2097152 的整数，或留空表示模型窗口未知'
      return
    }
    if (
      modelForm.model_type === 'chat' &&
      windowTokens !== null &&
      maxOutputTokens + 256 >= windowTokens
    ) {
      modalError.value = '最大输出与256 Token安全余量占满上下文窗口，请调整输出上限或窗口'
      return
    }
    if (modelForm.capacity_note.length > 2000 || modelForm.capacity_note.includes('\0')) {
      modalError.value = '容量依据最多2000个字符，且不能包含空字符'
      return
    }
    const editing = editingModel.value
    const context = mutationScope.replace()
    saving.value = true
    modalError.value = ''
    try {
      const body = {
        ...modelForm,
        vision_enabled: modelForm.model_type === 'chat' && modelForm.vision_enabled,
        max_output_tokens: maxOutputTokens,
        name: modelForm.name.trim(),
        upstream_model: modelForm.upstream_model.trim(),
        capacity_note: modelForm.capacity_note.trim()
      }
      const model = editing
        ? await updateModel(
            editing.id,
            {
              name: body.name,
              connection_id: body.connection_id,
              upstream_model: body.upstream_model,
              max_output_tokens: body.max_output_tokens,
              context_window_tokens: body.context_window_tokens,
              capacity_note: body.capacity_note,
              vision_enabled: body.model_type === 'chat' && body.vision_enabled
            },
            context.signal
          )
        : await createModel(body, context.signal)
      if (context.isCurrent()) {
        options.saved(model, editing !== null)
        modelOpen.value = false
        modelForm.name = ''
        modelForm.upstream_model = ''
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) modalError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  watch(options.access.authority, (current, previous) => {
    mutationScope.replace()
    saving.value = false
    modalError.value = ''
    if (current?.id !== previous?.id) {
      modelOpen.value = false
      editingModel.value = null
      Object.assign(modelForm, {
        name: '',
        connection_id: '',
        upstream_model: '',
        max_output_tokens: 512,
        context_window_tokens: null,
        capacity_note: '',
        model_type: 'chat',
        vision_enabled: false
      })
    } else if (!(editingModel.value ? canUpdateModels.value : canCreateModels.value)) {
      modelOpen.value = false
      editingModel.value = null
    }
  })
  onBeforeUnmount(() => mutationScope.dispose())
  return {
    modelOpen,
    editingModel,
    modelForm,
    saving,
    modalError,
    openModel,
    closeModel,
    saveModel,
    setConnectionId,
    setMaxOutputTokens,
    setModelType
  }
}
