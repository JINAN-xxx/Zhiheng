<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputPassword as AInputPassword
} from 'ant-design-vue'
import { LockOutlined, UserOutlined } from '@ant-design/icons-vue'
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
  <AuthShell class="login" split title="登录" description="统一模型接入、对话协作与开发者访问。">
    <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" />
    <AForm
      class="login-form"
      :model="loginForm"
      :rules="{
        username: [{ required: true, message: '请输入您的账号' }],
        password: [{ required: true, message: '请输入您的密码' }]
      }"
      layout="vertical"
      @finish="submit"
    >
      <AFormItem name="username"
        ><AInput
          v-model:value="loginForm.username"
          autocomplete="username"
          placeholder="账号"
          aria-label="账号"
          size="large"
          :maxlength="80"
          ><template #prefix><UserOutlined /></template></AInput
      ></AFormItem>
      <AFormItem name="password"
        ><AInputPassword
          v-model:value="loginForm.password"
          autocomplete="current-password"
          placeholder="密码"
          aria-label="密码"
          size="large"
          ><template #prefix><LockOutlined /></template></AInputPassword
      ></AFormItem>
      <div class="login-options">
        <ACheckbox disabled
          ><span title="请使用浏览器密码管理器保存密码；平台不保存登录密码"
            >记住密码</span
          ></ACheckbox
        >
      </div>
      <AButton
        type="primary"
        aria-label="登录"
        html-type="submit"
        size="large"
        block
        :loading="busy"
        >{{ busy ? '登录中...' : '登录' }}</AButton
      >
    </AForm>
    <template #footer>Copyright © 2024-2026 insistence.tech All Rights Reserved.</template>
  </AuthShell>
</template>
<style scoped>
.login-form {
  width: min(100%, 420px);
}

.login-options {
  display: flex;
  min-height: 32px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: -4px 0 16px;
}

.auth-code-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px;
  gap: 10px;
}

.auth-code {
  height: 40px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-50);
  cursor: pointer;
}

.auth-code img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

@media (max-width: 360px) {
  .auth-code-row {
    grid-template-columns: minmax(0, 1fr) 96px;
  }
}
</style>
