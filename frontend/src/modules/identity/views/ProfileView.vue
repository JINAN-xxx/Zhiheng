<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Avatar as AAvatar,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputPassword as AInputPassword,
  Tag as ATag,
  message
} from 'ant-design-vue'
import WorkspacePage from '../../../shared/components/WorkspacePage.vue'
import WorkspacePanel from '../../../shared/components/WorkspacePanel.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { changePassword, getProfile, updateProfile } from '../api/profile.ts'
import type { Profile } from '../api/profile.ts'
import { clearIdentity, identity } from '../public.ts'
import { profileUpdate, validatePasswordDraft, validateProfileDraft } from '../profileForm.ts'
import type { PasswordErrors, ProfileErrors } from '../profileForm.ts'

const router = useRouter()
const scope = new RequestScope()
const profile = ref<Profile | null>(null)
const draft = reactive({ display_name: '', email: '', phone: '' })
const passwords = reactive({ current_password: '', new_password: '', confirmation: '' })
const profileErrors = ref<ProfileErrors>({})
const passwordErrors = ref<PasswordErrors>({})
const busy = ref<'loading' | 'profile' | 'password' | null>(null)
const loadError = ref('')
const saveError = ref('')
const passwordError = ref('')
const conflict = ref(false)
const disabled = computed(() => busy.value !== null || conflict.value)
const profileName = computed(
  () => profile.value?.display_name.trim() || profile.value?.username || ''
)
const avatarLetter = computed(() => [...profileName.value][0]?.toUpperCase())
const createdAt = computed(() => {
  const value = profile.value?.created_at
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
})

function clearPasswords(): void {
  passwords.current_password = ''
  passwords.new_password = ''
  passwords.confirmation = ''
  passwordErrors.value = {}
}

function resetDraft(): void {
  draft.display_name = profile.value?.display_name ?? ''
  draft.email = profile.value?.email ?? ''
  draft.phone = profile.value?.phone ?? ''
  profileErrors.value = {}
  saveError.value = ''
}

function assertProfileOwner(value: Profile): void {
  const user = identity.value
  if (!user || value.id !== user.id || value.username !== user.username)
    throw new Error('返回的个人资料与当前账号不一致，请重新登录')
}

async function loadProfile(): Promise<void> {
  const user = identity.value
  if (!user || busy.value) return
  const context = scope.replace()
  busy.value = 'loading'
  loadError.value = ''
  clearPasswords()
  try {
    const value = await getProfile(context.signal)
    if (!context.isCurrent() || identity.value !== user) return
    assertProfileOwner(value)
    profile.value = value
    resetDraft()
    passwordError.value = ''
    conflict.value = false
  } catch (reason: unknown) {
    if (context.isCurrent() && identity.value === user && !isAborted(reason))
      loadError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent() && identity.value === user) busy.value = null
  }
}

async function saveProfile(): Promise<void> {
  const user = identity.value
  const current = profile.value
  if (!user || !current || disabled.value) return
  profileErrors.value = validateProfileDraft(draft)
  if (Object.keys(profileErrors.value).length) return
  const context = scope.replace()
  busy.value = 'profile'
  saveError.value = ''
  try {
    const value = await updateProfile(profileUpdate(draft, current.version), context.signal)
    if (!context.isCurrent() || identity.value !== user) return
    assertProfileOwner(value)
    profile.value = value
    resetDraft()
    message.success('基本资料已保存')
  } catch (reason: unknown) {
    if (!context.isCurrent() || identity.value !== user || isAborted(reason)) return
    if (reason instanceof ApiError && reason.status === 409) conflict.value = true
    else saveError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent() && identity.value === user) busy.value = null
  }
}

async function savePassword(): Promise<void> {
  const user = identity.value
  const current = profile.value
  if (!user || !current || disabled.value) return
  passwordErrors.value = validatePasswordDraft(passwords)
  if (Object.keys(passwordErrors.value).length) return
  const context = scope.replace()
  busy.value = 'password'
  passwordError.value = ''
  try {
    await changePassword(
      {
        current_password: passwords.current_password,
        new_password: passwords.new_password,
        expected_version: current.version
      },
      context.signal
    )
    if (!context.isCurrent() || identity.value !== user) return
    clearPasswords()
    clearIdentity()
    await router.replace({
      path: '/login',
      query: { passwordChanged: '1' }
    })
  } catch (reason: unknown) {
    if (!context.isCurrent() || identity.value !== user || isAborted(reason)) return
    if (reason instanceof ApiError && reason.status === 409) conflict.value = true
    else passwordError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent() && identity.value === user) {
      clearPasswords()
      busy.value = null
    }
  }
}

watch(
  identity,
  () => {
    scope.replace()
    profile.value = null
    busy.value = null
    loadError.value = ''
    saveError.value = ''
    passwordError.value = ''
    conflict.value = false
    resetDraft()
    clearPasswords()
    if (identity.value) void loadProfile()
  },
  { immediate: true, flush: 'sync' }
)
onBeforeUnmount(() => {
  scope.dispose()
  profile.value = null
  resetDraft()
  clearPasswords()
})
</script>

<template>
  <WorkspacePage class="profile-page" title="个人资料">
    <WorkspaceState v-if="busy === 'loading' && !profile" kind="loading" />
    <WorkspaceState
      v-else-if="!profile"
      kind="error"
      :message="loadError || '无法读取个人资料'"
      @retry="loadProfile"
    />
    <template v-else>
      <AAlert v-if="loadError" :message="loadError" type="error" show-icon class="profile-alert">
        <template #action><AButton size="small" @click="loadProfile">重试加载</AButton></template>
      </AAlert>
      <AAlert
        v-if="conflict"
        type="warning"
        message="资料已发生变更，当前草稿已保留。请重新加载最新资料后再保存。"
        show-icon
        class="profile-alert"
      >
        <template #action>
          <AButton :loading="busy === 'loading'" @click="loadProfile">放弃草稿并重新加载</AButton>
        </template>
      </AAlert>
      <div class="profile-layout">
        <WorkspacePanel class="profile-account" title="账号信息">
          <div class="profile-identity">
            <AAvatar :size="56">{{ avatarLetter }}</AAvatar>
            <div>
              <h2>{{ profileName }}</h2>
            </div>
          </div>
          <dl class="profile-facts">
            <div v-if="profileName !== profile.username">
              <dt>账号</dt>
              <dd>{{ profile.username }}</dd>
            </div>
            <div>
              <dt>角色</dt>
              <dd class="profile-roles">
                <ATag v-for="role in profile.roles" :key="role">{{ role }}</ATag>
                <span v-if="!profile.roles.length">未分配</span>
              </dd>
            </div>
            <div>
              <dt>部门</dt>
              <dd>{{ profile.department_name || '未分配' }}</dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>{{ createdAt }}</dd>
            </div>
          </dl>
        </WorkspacePanel>
        <div class="profile-settings">
          <WorkspacePanel title="基本资料">
            <AAlert
              v-if="saveError"
              :message="saveError"
              type="error"
              show-icon
              class="profile-alert"
            />
            <AForm
              name="profile"
              :model="draft"
              layout="vertical"
              :required-mark="false"
              @finish="saveProfile"
            >
              <AFormItem
                label="显示名称"
                html-for="profile-display-name"
                :validate-status="profileErrors.display_name ? 'error' : ''"
                :help="profileErrors.display_name ?? ''"
              >
                <AInput
                  id="profile-display-name"
                  v-model:value="draft.display_name"
                  autocomplete="nickname"
                  :disabled="disabled"
                  placeholder="输入显示名称"
                />
              </AFormItem>
              <div class="profile-contact-grid">
                <AFormItem
                  label="邮箱"
                  html-for="profile-email"
                  :validate-status="profileErrors.email ? 'error' : ''"
                  :help="profileErrors.email ?? ''"
                >
                  <AInput
                    id="profile-email"
                    v-model:value="draft.email"
                    autocomplete="email"
                    :disabled="disabled"
                    placeholder="输入联系邮箱"
                  />
                </AFormItem>
                <AFormItem
                  label="联系电话"
                  html-for="profile-phone"
                  :validate-status="profileErrors.phone ? 'error' : ''"
                  :help="profileErrors.phone ?? ''"
                >
                  <AInput
                    id="profile-phone"
                    v-model:value="draft.phone"
                    autocomplete="tel"
                    :disabled="disabled"
                    placeholder="输入联系电话"
                  />
                </AFormItem>
              </div>
              <div class="profile-actions">
                <AButton
                  type="primary"
                  html-type="submit"
                  :loading="busy === 'profile'"
                  :disabled="disabled"
                  >保存资料</AButton
                >
                <AButton :disabled="disabled" @click="resetDraft">取消修改</AButton>
              </div>
            </AForm>
          </WorkspacePanel>
          <WorkspacePanel title="账号安全" subtitle="修改密码后，需要在所有设备上重新登录">
            <AAlert
              v-if="passwordError"
              :message="passwordError"
              type="error"
              show-icon
              class="profile-alert"
            />
            <AForm
              name="password"
              :model="passwords"
              layout="vertical"
              :required-mark="false"
              @finish="savePassword"
            >
              <AFormItem
                label="当前密码"
                html-for="profile-current-password"
                :validate-status="passwordErrors.current_password ? 'error' : ''"
                :help="passwordErrors.current_password ?? ''"
              >
                <AInputPassword
                  id="profile-current-password"
                  v-model:value="passwords.current_password"
                  autocomplete="current-password"
                  :disabled="disabled"
                  placeholder="输入当前密码"
                />
              </AFormItem>
              <div class="profile-contact-grid">
                <AFormItem
                  label="新密码"
                  html-for="profile-new-password"
                  :validate-status="passwordErrors.new_password ? 'error' : ''"
                  :help="passwordErrors.new_password || '12–1024 个字符'"
                >
                  <AInputPassword
                    id="profile-new-password"
                    v-model:value="passwords.new_password"
                    autocomplete="new-password"
                    :disabled="disabled"
                    placeholder="输入新密码"
                  />
                </AFormItem>
                <AFormItem
                  label="确认新密码"
                  html-for="profile-confirm-password"
                  :validate-status="passwordErrors.confirmation ? 'error' : ''"
                  :help="passwordErrors.confirmation ?? ''"
                >
                  <AInputPassword
                    id="profile-confirm-password"
                    v-model:value="passwords.confirmation"
                    autocomplete="new-password"
                    :disabled="disabled"
                    placeholder="再次输入新密码"
                  />
                </AFormItem>
              </div>
              <div class="profile-actions">
                <AButton
                  type="primary"
                  html-type="submit"
                  :loading="busy === 'password'"
                  :disabled="disabled"
                  >修改密码</AButton
                >
                <AButton :disabled="disabled" @click="clearPasswords">清空</AButton>
              </div>
            </AForm>
          </WorkspacePanel>
        </div>
      </div>
    </template>
  </WorkspacePage>
</template>

<style scoped>
.profile-page {
  color: var(--color-text);
  font-family: var(--workspace-font-family);
  background: var(--bg-page);
}
.profile-alert {
  margin-bottom: var(--workspace-space-4);
}
.profile-layout {
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
  align-items: start;
  gap: var(--workspace-space-5);
}
.profile-settings {
  display: grid;
  min-width: 0;
  gap: var(--workspace-space-5);
}
.profile-identity {
  display: flex;
  align-items: center;
  gap: var(--workspace-space-3);
  margin-bottom: var(--workspace-space-5);
}
.profile-identity :deep(.ant-avatar) {
  flex-shrink: 0;
  background: var(--main-50);
  color: var(--main-color);
}
.profile-identity > div {
  min-width: 0;
}
.profile-identity h2 {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 16px;
  font-weight: var(--workspace-font-weight-medium);
}
.profile-facts {
  display: grid;
  gap: var(--workspace-space-4);
  margin: 0;
}
.profile-facts dt {
  margin-bottom: 5px;
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-helper);
}
.profile-facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.profile-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.profile-roles :deep(.ant-tag) {
  margin: 0;
}
.profile-contact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--workspace-space-4);
}
.profile-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--workspace-space-2);
}
@media (max-width: 1100px) {
  .profile-layout {
    grid-template-columns: minmax(220px, 260px) minmax(0, 1fr);
  }
  .profile-contact-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }
}
@media (max-width: 767px) {
  .profile-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .profile-page :deep(.ant-alert) {
    flex-wrap: wrap;
    gap: var(--workspace-space-2);
  }
}
</style>
