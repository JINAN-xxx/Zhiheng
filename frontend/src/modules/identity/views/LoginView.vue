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
  <main class="login-page">
    <section class="login-intro">
      <div class="brand-mark">衡</div>
      <p class="eyebrow">ZHIHENG · AI WORKSPACE</p>
      <h1>让灵感，<br />成为下一步。</h1>
      <p>连接模型，开始对话。<br />在一个专注的工作空间里，探索 AI 的可能。</p>
      <div class="intro-line" />
    </section>
    <section class="login-panel">
      <div class="login-box">
        <p class="eyebrow">欢迎回来</p>
        <h2>登录智衡</h2>
        <p class="muted">使用你的平台账号继续</p>
        <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" />
        <AForm :model="loginForm" layout="vertical" @finish="submit"
          ><AFormItem name="username" label="用户名" required
            ><AInput
              v-model:value="loginForm.username"
              autocomplete="username"
              placeholder="输入用户名"
              size="large"
              :maxlength="80" /></AFormItem
          ><AFormItem name="password" label="密码" required
            ><AInputPassword
              v-model:value="loginForm.password"
              autocomplete="current-password"
              placeholder="输入密码"
              size="large" /></AFormItem
          ><AButton
            type="primary"
            html-type="submit"
            size="large"
            block
            :loading="busy"
            :disabled="!loginForm.username.trim() || !loginForm.password"
            >登录工作空间</AButton
          ></AForm
        >
        <p class="login-note">账号由管理员分配</p>
      </div>
    </section>
  </main>
</template>
