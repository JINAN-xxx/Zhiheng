import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Modal } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  deleteSkill,
  getSkill,
  getSkillContent,
  listSkills,
  updateSkillStatus
} from '../api/index.ts'
import type { Skill } from '../api/index.ts'

interface ActionFailure {
  message: string
  details?: unknown
}

export function useSkillsPage() {
  const route = useRoute(),
    router = useRouter()
  const canManage = computed(() => identity.value !== null)
  const skills = ref<Skill[]>([]),
    total = ref(0),
    loading = ref(false),
    error = ref('')
  const detailOpen = ref(false),
    detail = ref<Skill | null>(null),
    content = ref('')
  const detailLoading = ref(false),
    detailError = ref('')
  const uploadOpen = ref(false),
    uploadTarget = ref<Skill | null>(null)
  const deletingId = ref('')
  const pending = reactive<Record<string, boolean>>({})
  const failures = reactive<Record<string, ActionFailure | undefined>>({})
  const query = reactive({
    keyword: '',
    status: undefined as string | undefined,
    pageNum: 1,
    pageSize: 24
  })
  const listScope = new RequestScope(),
    detailScope = new RequestScope(),
    deletionScope = new RequestScope()
  const statusScopes = new Map<string, RequestScope>()
  let closeConfirmation: (() => void) | undefined
  let requestedQuery = ''

  const saving = computed(() => Boolean(detail.value && pending[detail.value.skillId]))
  const deleting = computed(() => deletingId.value !== '')
  const actionError = computed(() => failures[detail.value?.skillId ?? '']?.message ?? '')
  const actionDetails = computed(() => failures[detail.value?.skillId ?? '']?.details)
  const queryKey = () => JSON.stringify(query)

  async function load(): Promise<void> {
    const context = listScope.replace()
    requestedQuery = queryKey()
    loading.value = true
    error.value = ''
    try {
      const result = await listSkills(query, context.signal)
      if (context.isCurrent()) {
        skills.value = result.rows
        total.value = result.total
        ensureVisiblePage()
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }

  function search(): void {
    query.pageNum = 1
    void load()
  }

  function changeStatus(value: unknown): void {
    query.status = value === 'enabled' || value === 'disabled' ? value : undefined
    search()
  }

  function ensureVisiblePage(): void {
    const lastPage = Math.max(1, Math.ceil(total.value / query.pageSize))
    if (query.pageNum > lastPage) {
      query.pageNum = lastPage
      void load()
    }
  }

  function syncSkill(item: Skill): void {
    const previous = skills.value.find((skill) => skill.skillId === item.skillId)
    if (previous && previous.lockVersion <= item.lockVersion) {
      if (query.status && query.status !== item.status) {
        skills.value = skills.value.filter((skill) => skill.skillId !== item.skillId)
        total.value = Math.max(0, total.value - 1)
        ensureVisiblePage()
      } else
        skills.value = skills.value.map((skill) => (skill.skillId === item.skillId ? item : skill))
    }
    if (detail.value?.skillId === item.skillId && detail.value.lockVersion <= item.lockVersion)
      detail.value = item
  }

  function afterMutation(startedQuery: string): void {
    const interrupted = loading.value
    listScope.replace()
    loading.value = false
    if (interrupted || requestedQuery !== startedQuery) void load()
  }

  async function toggleStatus(item: Skill): Promise<void> {
    const id = item.skillId
    if (!canManage.value || pending[id] || deletingId.value === id) return
    const scope = statusScopes.get(id) ?? new RequestScope()
    statusScopes.set(id, scope)
    const context = scope.replace(),
      startedQuery = requestedQuery
    pending[id] = true
    failures[id] = undefined
    try {
      const updated = await updateSkillStatus(
        id,
        item.status === 'enabled' ? 'disabled' : 'enabled',
        item.lockVersion,
        context.signal
      )
      if (context.isCurrent()) {
        afterMutation(startedQuery)
        syncSkill(updated)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason))
        failures[id] = {
          message: errorMessage(reason),
          details: reason instanceof ApiError ? reason.details : undefined
        }
    } finally {
      if (context.isCurrent()) pending[id] = false
    }
  }

  async function loadDetail(): Promise<void> {
    const item = detail.value
    if (!detailOpen.value || !item) return
    const context = detailScope.replace()
    detailLoading.value = true
    detailError.value = ''
    try {
      const [updated, text] = await Promise.all([
        getSkill(item.skillId, context.signal),
        getSkillContent(item.skillId, context.signal)
      ])
      if (context.isCurrent() && detail.value?.skillId === item.skillId) {
        syncSkill(updated)
        content.value = text
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) detailError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) detailLoading.value = false
    }
  }

  function setDetailOpen(open: boolean): void {
    detailOpen.value = open
    if (!open) {
      closeConfirmation?.()
      detailScope.replace()
      detail.value = null
      content.value = ''
      detailError.value = ''
      detailLoading.value = false
    }
  }

  function openDetail(item: Skill): void {
    closeConfirmation?.()
    detail.value = item
    detailOpen.value = true
    content.value = ''
    void loadDetail()
  }

  function openUpload(target: Skill | null = null): void {
    if (!canManage.value) return
    uploadTarget.value = target
    setDetailOpen(false)
    uploadOpen.value = true
  }

  function updateSelected(): void {
    if (detail.value) openUpload(detail.value)
  }

  function toggleSelected(): void {
    if (detail.value) void toggleStatus(detail.value)
  }

  function installed(): void {
    query.pageNum = 1
    void load()
  }

  async function removeSelected(): Promise<void> {
    const item = detail.value
    if (!item || !canManage.value || deletingId.value || pending[item.skillId]) return
    const context = deletionScope.replace(),
      startedQuery = requestedQuery
    deletingId.value = item.skillId
    failures[item.skillId] = undefined
    try {
      const accepted = await new Promise<boolean>((resolve) => {
        const dialog = Modal.confirm({
          title: `确认删除技能“${item.skillName}”？`,
          content: '删除后将停止新使用，历史内容和运行记录仍会保留。',
          okText: '确定',
          cancelText: '取消',
          okType: 'danger',
          onOk: () => {
            closeConfirmation = undefined
            resolve(true)
          },
          onCancel: () => {
            closeConfirmation = undefined
            resolve(false)
          }
        })
        closeConfirmation = () => {
          closeConfirmation = undefined
          dialog.destroy()
          resolve(false)
        }
      })
      if (!accepted || !context.isCurrent() || !canManage.value) return
      await deleteSkill(item.skillId, context.signal)
      if (context.isCurrent()) {
        afterMutation(startedQuery)
        if (skills.value.some((skill) => skill.skillId === item.skillId)) {
          skills.value = skills.value.filter((skill) => skill.skillId !== item.skillId)
          total.value = Math.max(0, total.value - 1)
        }
        if (detail.value?.skillId === item.skillId) setDetailOpen(false)
        ensureVisiblePage()
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason))
        failures[item.skillId] = {
          message: errorMessage(reason),
          details: reason instanceof ApiError ? reason.details : undefined
        }
    } finally {
      if (context.isCurrent()) deletingId.value = ''
    }
  }

  watch(
    [() => route.query.action, canManage],
    ([action, allowed]) => {
      if (action !== 'upload' || !allowed) return
      openUpload()
      const updated = { ...route.query }
      delete updated.action
      void router.replace({ query: updated })
    },
    { immediate: true }
  )

  onMounted(load)
  onBeforeUnmount(() => {
    closeConfirmation?.()
    listScope.dispose()
    detailScope.dispose()
    deletionScope.dispose()
    for (const scope of statusScopes.values()) scope.dispose()
    statusScopes.clear()
  })

  return {
    skills,
    total,
    loading,
    error,
    query,
    canManage,
    pending,
    failures,
    detailOpen,
    detail,
    content,
    detailLoading,
    detailError,
    saving,
    deleting,
    actionError,
    actionDetails,
    uploadOpen,
    uploadTarget,
    load,
    search,
    changeStatus,
    openDetail,
    setDetailOpen,
    toggleStatus,
    toggleSelected,
    openUpload,
    updateSelected,
    installed,
    loadDetail,
    removeSelected
  }
}
