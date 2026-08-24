<template>
  <AuthShell
    class="register auth-shell"
    :brand="title"
    :title="`注册 ${title}`"
    description="创建账户后即可接入模型、管理访问并开始智能协作。"
  >
    <a-form
      class="register-form"
      layout="vertical"
      :model="registerForm"
      :rules="registerRules"
      @finish="handleRegister"
    >
      <a-form-item name="username">
        <a-input
          v-model:value="registerForm.username"
          autocomplete="username"
          placeholder="账号"
          size="large"
        >
          <template #prefix><UserOutlined /></template>
        </a-input>
      </a-form-item>

      <a-form-item name="password" :rules="registerPwdValidator">
        <a-input-password
          v-model:value="registerForm.password"
          autocomplete="new-password"
          placeholder="密码"
          size="large"
        >
          <template #prefix><LockOutlined /></template>
        </a-input-password>
      </a-form-item>

      <a-form-item name="confirmPassword">
        <a-input-password
          v-model:value="registerForm.confirmPassword"
          autocomplete="new-password"
          placeholder="确认密码"
          size="large"
        >
          <template #prefix><LockOutlined /></template>
        </a-input-password>
      </a-form-item>

      <a-form-item v-if="captchaEnabled" name="code">
        <div class="auth-code-row">
          <a-input
            v-model:value="registerForm.code"
            autocomplete="off"
            placeholder="验证码"
            size="large"
          >
            <template #prefix><SafetyCertificateOutlined /></template>
          </a-input>
          <button class="auth-code" type="button" aria-label="刷新验证码" @click="getCode">
            <img :src="codeUrl" alt="验证码" />
          </button>
        </div>
      </a-form-item>

      <a-button block html-type="submit" :loading="loading" size="large" type="primary">
        {{ loading ? '注册中...' : '注册' }}
      </a-button>
      <div class="register-login">
        <router-link to="/login">使用已有账户登录</router-link>
      </div>
    </a-form>

    <template #footer>{{ footerContent }}</template>
  </AuthShell>
</template>

<script setup>
import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons-vue'

import { getCodeImg, register } from '@/api/login'
import { AuthShell } from '@/components/Antd'
import feedback from '@/plugins/antdFeedback'
import defaultSettings from '@/settings'
import { usePasswordRule } from '@/utils/passwordRule'

const title = import.meta.env.VITE_APP_TITLE
const footerContent = defaultSettings.footerContent
const router = useRouter()
const { registerPwdValidator } = usePasswordRule()

const registerForm = ref({
  username: '',
  password: '',
  confirmPassword: '',
  code: '',
  uuid: '',
})

const equalToPassword = (_rule, value) => {
  if (registerForm.value.password !== value) {
    return Promise.reject(new Error('两次输入的密码不一致'))
  }
  return Promise.resolve()
}

const registerRules = {
  username: [
    { required: true, trigger: 'blur', message: '请输入您的账号' },
    { min: 2, max: 20, message: '用户账号长度必须介于 2 和 20 之间', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, trigger: 'blur', message: '请再次输入您的密码' },
    { validator: equalToPassword, trigger: 'blur' },
  ],
  code: [{ required: true, trigger: 'change', message: '请输入验证码' }],
}

const codeUrl = ref('')
const loading = ref(false)
const captchaEnabled = ref(true)

async function handleRegister() {
  loading.value = true
  try {
    await register(registerForm.value)
    const username = registerForm.value.username
    await feedback.alertSuccess(`恭喜您，账号 ${username} 注册成功！`)
    await router.push('/login')
  } catch {
    loading.value = false
    if (captchaEnabled.value) await getCode()
  }
}

async function getCode() {
  const response = await getCodeImg()
  captchaEnabled.value = response.captchaEnabled ?? true
  if (captchaEnabled.value) {
    codeUrl.value = `data:image/gif;base64,${response.img}`
    registerForm.value.uuid = response.uuid
  }
}

getCode()
</script>

<style scoped>
.register-form {
  width: min(100%, 420px);
}

.register-login {
  margin-top: 18px;
  text-align: center;
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
