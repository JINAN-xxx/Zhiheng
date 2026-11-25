<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Modal as AModal,
  Select as ASelect,
  Checkbox as ACheckbox,
  Alert as AAlert
} from 'ant-design-vue'
import { listBases } from '../../knowledge/public.ts'
import { canUseCapability, identity } from '../../identity/public.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { listPlazaDepartments, publishAssistant } from '../api/index.ts'
import type { Agent } from '../api/index.ts'

const props = defineProps<{ agent: Agent | null }>()
const emit = defineEmits<{ close: []; published: [] }>()
const scope = new RequestScope()
const department = ref('')
const departmentOptions = ref<{ value: string; label: string }[]>([])
const titles = ref<string[]>([])
const consent = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const ready = ref(false)
const title = computed(() => (props.agent?.plazaVersion ? '更新广场版本' : '上架广场'))

async function load(): Promise<void> {
  const agent = props.agent
  const context = scope.replace()
  loading.value = saving.value = ready.value = consent.value = false
  titles.value = []
  departmentOptions.value = []
  error.value = ''
  if (!agent) return
  loading.value = true
  department.value = agent.plazaDepartmentId ?? ''
  try {
    const [departments, bases] = await Promise.all([
      listPlazaDepartments(context.signal),
      agent.knowledgeBaseIds.length ? listBases(context.signal) : Promise.resolve([])
    ])
    if (!context.isCurrent()) return
    departmentOptions.value = [
      { value: '', label: '所有可使用助手的账号' },
      ...departments.map((item) => ({ value: item.id, label: item.name }))
    ]
    if (department.value && !departments.some((item) => item.id === department.value)) {
      departmentOptions.value.push({ value: department.value, label: '原部门已停用' })
    }
    const own = new Map(
      bases.filter((base) => base.owner_id === agent.ownerId).map((base) => [base.id, base.name])
    )
    titles.value = agent.knowledgeBaseIds.flatMap((id) => {
      const name = own.get(id)
      return name === undefined ? [] : [name]
    })
    if (titles.value.length !== agent.knowledgeBaseIds.length) {
      error.value = '只能授权本人拥有且可用的知识库'
      return
    }
    ready.value = true
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}

async function publish(): Promise<void> {
  const agent = props.agent
  if (
    !agent ||
    !ready.value ||
    saving.value ||
    (titles.value.length > 0 && !consent.value) ||
    !canUseCapability('agents', 'plaza_publish') ||
    identity.value?.id !== agent.ownerId
  )
    return
  const context = scope.replace()
  saving.value = true
  error.value = ''
  try {
    await publishAssistant(
      agent,
      { department_id: department.value || null, allow_knowledge: consent.value },
      context.signal
    )
    if (context.isCurrent()) emit('published')
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}

watch(() => props.agent, load, { immediate: true })
onBeforeUnmount(() => scope.dispose())
</script>

<template>
  <a-modal
    :open="!!agent"
    :title="title"
    width="480px"
    :ok-text="title"
    cancel-text="取消"
    :confirm-loading="saving"
    :ok-button-props="{ disabled: !ready || (titles.length > 0 && !consent) }"
    @ok="publish"
    @cancel="emit('close')"
  >
    <div class="publication-form">
      <label for="plaza-department">使用范围</label>
      <a-select
        id="plaza-department"
        :value="department"
        :options="departmentOptions"
        :loading="loading"
        :disabled="loading || saving"
        aria-label="使用范围"
        @update:value="
          (value) => {
            if (typeof value === 'string') department = value
          }
        "
      />
      <template v-if="titles.length">
        <span>允许检索的知识库</span>
        <ul>
          <li v-for="(name, index) in titles" :key="index">{{ name }}</li>
        </ul>
        <a-checkbox v-model:checked="consent" :disabled="saving"
          >允许上述范围内的用户通过此助手检索这些知识库</a-checkbox
        >
      </template>
      <a-alert v-if="error" type="error" show-icon :message="error" />
    </div>
  </a-modal>
</template>

<style scoped>
.publication-form {
  display: grid;
  min-width: 0;
  gap: 10px;
  margin-block: 12px;
}
.publication-form ul {
  max-height: 156px;
  margin: 0;
  overflow: auto;
  overflow-wrap: anywhere;
  padding-inline-start: 20px;
}
</style>
