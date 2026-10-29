<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputPassword as AInputPassword
} from 'ant-design-vue'
import AuthShell from '../../../shared/components/AuthShell.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { login } from '../public.ts'
const router = useRouter()
const route = useRoute()
const loginForm = reactive({ username: '', password: '' })
const busy = ref(false)
const error = ref(typeof route.query.reason === 'string' ? route.query.reason : '')
const scope = new RequestScope()
onBeforeUnmount(() => {
  scope.dispose()
  loginForm.password = ''
})
async function submit(): Promise<void> {
  if (busy.value || !loginForm.username.trim() || !loginForm.password) return
  const context = scope.replace()
  busy.value = true
  error.value = ''
  try {
    await login(loginForm.username.trim(), loginForm.password, context.signal)
    if (context.isCurrent()) await router.replace('/chat')
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    loginForm.password = ''
    if (context.isCurrent()) busy.value = false
  }
}
</script>
<template>
  <AuthShell title="登录" description="使用账号继续">
    <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" />
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
    <p class="login-help">账号由管理员分配</p>
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

.login-help {
  margin: 24px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
}
</style>
