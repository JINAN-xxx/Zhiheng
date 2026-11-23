import { computed, ref } from 'vue'
import { message as notice } from 'ant-design-vue'
import { canUseCapability, hasAnyPermission, identity } from '../../identity/public.ts'

export function useModelAccess() {
  const canCreateModels = computed(() => canUseCapability('models', 'create'))
  const canUpdateModels = computed(() => canUseCapability('models', 'update'))
  const canDeleteModels = computed(() => canUseCapability('models', 'delete'))
  const canToggleModels = computed(() => canUseCapability('models', 'toggle'))
  const canCreateCredentials = computed(() => canUseCapability('credentials', 'create'))
  const canUpdateCredentials = computed(() => canUseCapability('credentials', 'update'))
  const canDeleteCredentials = computed(() => canUseCapability('credentials', 'delete'))
  const canToggleCredentials = computed(() => canUseCapability('credentials', 'toggle'))
  const canReadModels = computed(() =>
    hasAnyPermission(
      identity.value,
      'platform:models:config_read',
      'platform:models:create',
      'platform:models:update',
      'platform:models:delete',
      'platform:models:toggle'
    )
  )
  const canReadCredentials = computed(() => hasAnyPermission(identity.value, 'ai:credentials:read'))
  const trialDenied = ref(false)
  const canTestModels = computed(
    () => !trialDenied.value && hasAnyPermission(identity.value, 'platform:models:test')
  )
  function denyTrialAccess() {
    trialDenied.value = true
    void notice.error('模型测试权限已变化，请刷新页面后查看')
  }
  const canReadRates = computed(() => hasAnyPermission(identity.value, 'ai:rates:read'))
  const canCreateRates = computed(() => hasAnyPermission(identity.value, 'ai:rates:create'))
  const hasModelActions = computed(
    () =>
      canUpdateModels.value ||
      canDeleteModels.value ||
      canToggleModels.value ||
      canTestModels.value ||
      canReadRates.value
  )
  const authority = computed(() => identity.value)
  return {
    authority,
    canCreateModels,
    canUpdateModels,
    canDeleteModels,
    canToggleModels,
    canCreateCredentials,
    canUpdateCredentials,
    canDeleteCredentials,
    canToggleCredentials,
    canReadModels,
    canReadCredentials,
    canTestModels,
    denyTrialAccess,
    canReadRates,
    canCreateRates,
    hasModelActions
  }
}
export type ModelAccess = ReturnType<typeof useModelAccess>
