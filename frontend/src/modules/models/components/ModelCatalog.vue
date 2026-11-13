<script setup lang="ts">
import { Switch as ASwitch } from 'ant-design-vue'
import type { ChatModel } from '../api/index.ts'
import { modelTypeLabel } from '../presentation.ts'
import ModelIdentityIcon from './ModelIdentityIcon.vue'

const props = defineProps<{
  models: ChatModel[]
  connectionLabels: Record<string, string>
  connectionIssues: Record<string, string>
  errors: Record<string, string>
  canWrite: boolean
  pending: Record<string, boolean>
}>()
const emit = defineEmits<{
  open: [model: ChatModel]
  toggle: [model: ChatModel]
}>()

function toggle(model: ChatModel): void {
  if (props.canWrite && !props.pending[model.id]) emit('toggle', model)
}
</script>

<template>
  <div class="model-catalog">
    <div class="model-grid">
      <article v-for="model in models" :key="model.id" class="model-item">
        <button
          type="button"
          class="model-item__open"
          :aria-label="`查看模型详情：${model.name}`"
          @click="emit('open', model)"
        >
          <ModelIdentityIcon :type="model.model_type" />
          <span class="model-item__text">
            <strong :title="model.name">{{ model.name }}</strong>
            <span class="model-item__description" :title="connectionLabels[model.id]">
              {{ modelTypeLabel(model.model_type) }}模型<template v-if="connectionLabels[model.id]">
                · {{ connectionLabels[model.id] }}</template
              >
            </span>
            <span
              v-if="(!model.enabled && !canWrite) || connectionIssues[model.id]"
              class="model-item__notices"
            >
              <span v-if="!model.enabled && !canWrite" class="model-item__disabled">已停用</span>
              <span
                v-if="connectionIssues[model.id]"
                class="model-item__connection-issue"
                :title="connectionIssues[model.id]"
                >连接异常 · {{ connectionIssues[model.id] }}</span
              >
            </span>
          </span>
        </button>
        <ASwitch
          v-if="canWrite"
          :checked="model.enabled"
          :aria-label="`启用模型“${model.name}”`"
          :loading="pending[model.id] ?? false"
          :disabled="pending[model.id] ?? false"
          @change="toggle(model)"
        />
        <p v-if="errors[model.id]" class="model-item__error" role="alert">
          {{ errors[model.id] }}
        </p>
      </article>
    </div>
  </div>
</template>

<style scoped>
.model-catalog {
  min-width: 0;
  container-type: inline-size;
}
.model-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px 28px;
  align-content: start;
}
@container (min-width: 1360px) {
  .model-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@container (max-width: 960px) {
  .model-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@container (max-width: 560px) {
  .model-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.model-item {
  display: grid;
  min-width: 0;
  min-height: 88px;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px 16px;
  padding: 10px 12px;
  border-radius: 10px;
}
.model-item:hover {
  background: var(--gray-25);
}
.model-item__open {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 14px;
  padding: 4px 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.model-item__open:focus-visible {
  outline: 2px solid var(--main-400);
  outline-offset: 4px;
}
.model-item__text {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 5px;
}
.model-item__text strong,
.model-item__description,
.model-item__connection-issue {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-item__text strong {
  color: var(--color-text);
  font-size: 15px;
  font-weight: 500;
  line-height: 22px;
}
.model-item__description {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 20px;
}
.model-item__notices {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 18px;
}
.model-item__disabled {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}
.model-item__connection-issue {
  color: var(--color-warning, #ad6800);
}
.model-item__error {
  min-width: 0;
  grid-column: 1 / -1;
  margin: 0;
  padding-left: 62px;
  color: var(--color-error);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.model-item :deep(.ant-switch) {
  flex-shrink: 0;
}
</style>
