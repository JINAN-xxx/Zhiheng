<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputPassword as AInputPassword,
  Modal as AModal,
  message
} from 'ant-design-vue'
import AuthShell from '../../../shared/components/AuthShell.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { loginDestination } from '../navigation.ts'
import { identity, login } from '../public.ts'
import { savePasswordInBrowser } from '../browserPasswords.ts'
const router = useRouter()
const route = useRoute()
const loginForm = reactive({ username: '', password: '' })
const busy = ref(false)
const savePassword = ref(false)
const recoveryOpen = ref(false)
const error = ref('')
const navigationError = ref(typeof route.query.reason === 'string' ? route.query.reason : '')
const scope = new RequestScope()
onMounted(() => {
  if (!Object.prototype.hasOwnProperty.call(route.query, 'reason')) return
  const query = { ...route.query }
  delete query.reason
  // A navigation failure is a one-time notice, not a persistent login-page state.
  void router.replace({ path: route.path, query, hash: route.hash })
})
onBeforeUnmount(() => {
  scope.dispose()
  loginForm.password = ''
})
async function submit(): Promise<void> {
  if (busy.value || !loginForm.username.trim() || !loginForm.password) return
  const context = scope.replace()
  busy.value = true
  error.value = ''
  navigationError.value = ''
  try {
    const username = loginForm.username.trim()
    await login(username, loginForm.password, context.signal)
    if (!context.isCurrent()) return
    if (savePassword.value) {
      const authenticatedUser = identity.value
      // Browser confirmation may outlive this page; it must never delay login.
      void savePasswordInBrowser(window, username, loginForm.password).then((result) => {
        if (result !== 'requested' && authenticatedUser && identity.value === authenticatedUser)
          message.info('请使用浏览器的密码管理器保存密码')
      })
    }
    await router.replace(loginDestination(route.query.redirect))
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    loginForm.password = ''
    if (context.isCurrent()) busy.value = false
  }
}
</script>
<template>
  <AuthShell title="登录">
    <AAlert
      v-if="error"
      :message="error"
      type="error"
      show-icon
      closable
      class="space-bottom"
      @close="error = ''"
    />
    <AAlert
      v-else-if="navigationError"
      :message="'上次访问未成功：' + navigationError"
      type="warning"
      show-icon
      closable
      class="space-bottom"
      @close="navigationError = ''"
    />
    <AAlert
      v-else-if="route.query.passwordChanged === '1'"
      message="密码修改成功，请使用新密码重新登录"
      type="success"
      show-icon
      class="space-bottom"
    />
    <AForm
      name="login"
      class="login-form"
      :model="loginForm"
      :required-mark="false"
      :rules="{
        username: [{ required: true, message: '请输入您的账号' }],
        password: [{ required: true, message: '请输入您的密码' }]
      }"
      layout="vertical"
      @finish="submit"
    >
      <AFormItem label="账号" name="username" html-for="login-username">
        <AInput
          id="login-username"
          v-model:value="loginForm.username"
          autocomplete="username"
          placeholder="账号"
          aria-label="账号"
          size="large"
          :maxlength="80"
          :disabled="busy"
        />
      </AFormItem>
      <AFormItem label="密码" name="password" html-for="login-password">
        <AInputPassword
          id="login-password"
          v-model:value="loginForm.password"
          autocomplete="current-password"
          placeholder="密码"
          aria-label="密码"
          size="large"
          :disabled="busy"
        />
      </AFormItem>
      <div class="login-options">
        <span title="由浏览器密码管理器保存">
          <ACheckbox v-model:checked="savePassword" :disabled="busy">保存密码</ACheckbox>
        </span>
        <button type="button" class="login-recovery" :disabled="busy" @click="recoveryOpen = true">
          忘记密码
        </button>
      </div>
      <AButton
        class="login-submit"
        type="primary"
        aria-label="登录"
        html-type="submit"
        size="large"
        block
        :loading="busy"
        >{{ busy ? '登录中…' : '登录' }}</AButton
      >
    </AForm>
    <AModal
      :open="recoveryOpen"
      title="忘记密码"
      centered
      :width="400"
      :style="{ maxWidth: 'calc(100vw - 32px)' }"
      @cancel="recoveryOpen = false"
    >
      <p>请联系平台管理员，通过以下入口重置密码：</p>
      <p class="login-recovery-path">后台管理 → 用户管理</p>
      <p>重置后，使用新密码登录。</p>
      <template #footer
        ><AButton type="primary" @click="recoveryOpen = false">知道了</AButton></template
      >
    </AModal>
  </AuthShell>
</template>
<style scoped>
.login-form {
  width: 100%;
}

.login-form :deep(.ant-form-item) {
  margin-bottom: 24px;
}

.login-form :deep(.ant-form-item-label) {
  padding-bottom: 10px;
}

.login-form :deep(.ant-input-lg),
.login-form :deep(.ant-input-affix-wrapper-lg) {
  min-height: 48px;
  padding: 11px 14px;
  border-color: var(--gray-200);
  border-radius: 8px;
  font-size: 14px;
}

.login-form :deep(.ant-input-affix-wrapper-lg > .ant-input) {
  min-height: 0;
  padding: 0;
}

.login-form :deep(.ant-input-lg:hover),
.login-form :deep(.ant-input-affix-wrapper-lg:hover) {
  border-color: var(--main-400);
}

.login-form :deep(.ant-input-lg:focus),
.login-form :deep(.ant-input-affix-wrapper-focused) {
  border-color: var(--main-color);
}

.login-submit {
  height: 48px;
  margin-top: 4px;
  border-radius: 8px;
  font-size: 15px;
  font-weight: var(--workspace-font-weight-medium);
  box-shadow: none;
}

.login-options {
  display: flex;
  min-height: 24px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 20px;
}

.login-recovery {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-color);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}

.login-recovery:hover {
  color: var(--main-800);
}

.login-recovery:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 4px;
}

.login-recovery:disabled {
  color: var(--color-text-tertiary);
  cursor: default;
}

.login-recovery-path {
  color: var(--color-text);
  font-weight: var(--workspace-font-weight-medium);
}
</style>
