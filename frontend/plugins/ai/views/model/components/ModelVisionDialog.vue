<template>
  <a-modal
    v-model:open="visible" title="图片理解设置" width="min(520px, 94vw)" destroy-on-close
    :closable="!saving" :mask-closable="!saving" :keyboard="!saving"
  >
    <p>{{ model?.displayName || model?.modelCode }}</p>
    <a-spin :spinning="loading">
      <a-alert v-if="loadError" :message="loadError" type="error" show-icon>
        <template #action><a-button type="link" @click="load">重试</a-button></template>
      </a-alert>
      <a-form v-else layout="vertical">
        <a-form-item label="支持图片理解">
          <a-switch v-model:checked="vision" :disabled="loading || saving" aria-label="支持图片理解" />
        </a-form-item>
        <p class="vision-description">模型支持视觉输入时开启，可在对话中上传图片。请以服务商说明为准，开启此项不会让纯文本模型获得视觉能力。</p>
      </a-form>
      <a-alert v-if="saveError" :message="saveError" type="error" show-icon />
    </a-spin>
    <template #footer>
      <a-button :disabled="saving" @click="visible = false">取消</a-button>
      <a-button type="primary" :loading="saving" :disabled="loading || Boolean(loadError) || !loaded" @click="save">保存</a-button>
    </template>
  </a-modal>
</template>

<script setup>
import feedback from '@/plugins/antdFeedback'
import { getPlatformModel, updatePlatformModel } from '../../../api/modelCatalog.js'
import { buildModelVisionUpdate, createRequestGate } from '../../../utils/modelEngine.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  model: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const loading = ref(false)
const saving = ref(false)
const loaded = ref(false)
const vision = ref(false)
const loadError = ref('')
const saveError = ref('')
const gate = createRequestGate()

async function load() {
  const generation = gate.begin()
  loaded.value = false; loading.value = true; loadError.value = ''; saveError.value = ''
  try {
    const { data } = await getPlatformModel(props.model.modelId)
    if (!gate.isCurrent(generation)) return
    if (data?.modelType !== 'chat') throw new Error('模型类型不支持图片理解')
    vision.value = data.capabilities?.vision === true
    loaded.value = true
  } catch {
    if (gate.isCurrent(generation)) loadError.value = '图片理解设置加载失败，请重试'
  } finally {
    if (gate.isCurrent(generation)) loading.value = false
  }
}

async function save() {
  if (saving.value || loading.value || !loaded.value || !props.model?.modelId) return
  const generation = gate.begin()
  const modelId = props.model.modelId
  const enabled = vision.value
  saving.value = true; saveError.value = ''
  try {
    // Refresh before merging because the endpoint replaces all capabilities.
    const { data } = await getPlatformModel(modelId)
    if (!gate.isCurrent(generation)) return
    await updatePlatformModel(modelId, buildModelVisionUpdate(data, enabled))
    if (!gate.isCurrent(generation)) return
    feedback.msgSuccess('图片理解设置已保存')
    visible.value = false
    emit('saved')
  } catch {
    if (gate.isCurrent(generation)) saveError.value = '保存失败，请重试'
  } finally {
    saving.value = false
  }
}

watch(() => [props.modelValue, props.model?.modelId], ([open, modelId]) => {
  if (open && modelId) load()
  else gate.begin()
}, { immediate: true })
onBeforeUnmount(() => gate.invalidate())
</script>

<style scoped>
.vision-description { color: var(--color-text-secondary); line-height: 1.6; }
</style>
