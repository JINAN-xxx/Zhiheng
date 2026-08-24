<template>
  <AuthShell
    class="login auth-shell"
    :brand="title"
    :title="`登录 ${title}`"
    description="统一模型接入、对话协作与开发者访问。"
  >
    <a-form
      class="login-form"
      layout="vertical"
      :model="loginForm"
      :rules="loginRules"
      @finish="handleLogin"
    >
      <a-form-item name="username">
        <a-input
          v-model:value="loginForm.username"
          autocomplete="username"
          placeholder="账号"
          size="large"
        >
          <template #prefix><UserOutlined /></template>
        </a-input>
      </a-form-item>

      <a-form-item name="password">
        <a-input-password
          v-model:value="loginForm.password"
          autocomplete="current-password"
          placeholder="密码"
          size="large"
        >
          <template #prefix><LockOutlined /></template>
        </a-input-password>
      </a-form-item>

      <a-form-item v-if="captchaEnabled" name="code">
        <div class="auth-code-row">
          <a-input
            v-model:value="loginForm.code"
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

      <div class="login-options">
        <a-checkbox v-model:checked="loginForm.rememberMe">记住密码</a-checkbox>
        <router-link v-if="register" to="/register">立即注册</router-link>
      </div>

      <a-button block html-type="submit" :loading="loading" size="large" type="primary">
        {{ loading ? '登录中...' : '登录' }}
      </a-button>
    </a-form>

    <template #footer>{{ footerContent }}</template>
  </AuthShell>
</template>

<script setup>
import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons-vue'
import Cookies from 'js-cookie'

import { getCodeImg } from '@/api/login'
import { AuthShell } from '@/components/Antd'
import defaultSettings from '@/settings'
import useUserStore from '@/store/modules/user'
import { decrypt, encrypt } from '@/utils/jsencrypt'

const title = import.meta.env.VITE_APP_TITLE
const footerContent = defaultSettings.footerContent
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const loginForm = ref({
  username: '',
  password: '',
  rememberMe: false,
  code: '',
  uuid: '',
})

const loginRules = {
  username: [{ required: true, trigger: 'blur', message: '请输入您的账号' }],
  password: [{ required: true, trigger: 'blur', message: '请输入您的密码' }],
  code: [{ required: true, trigger: 'change', message: '请输入验证码' }],
}

const codeUrl = ref('')
const loading = ref(false)
const captchaEnabled = ref(true)
const register = ref(false)
const redirect = ref()

watch(
  route,
  (newRoute) => {
    redirect.value = newRoute.query?.redirect
  },
  { immediate: true },
)

async function handleLogin() {
  loading.value = true
  if (loginForm.value.rememberMe) {
    Cookies.set('username', loginForm.value.username, { expires: 30 })
    Cookies.set('password', encrypt(loginForm.value.password), { expires: 30 })
    Cookies.set('rememberMe', loginForm.value.rememberMe, { expires: 30 })
  } else {
    Cookies.remove('username')
    Cookies.remove('password')
    Cookies.remove('rememberMe')
  }

  try {
    await userStore.login(loginForm.value)
    const otherQueryParams = Object.keys(route.query).reduce((params, key) => {
      if (key !== 'redirect') params[key] = route.query[key]
      return params
    }, {})
    await router.push({ path: redirect.value || '/', query: otherQueryParams })
  } catch {
    loading.value = false
    if (captchaEnabled.value) await getCode()
  }
}

async function getCode() {
  const response = await getCodeImg()
  captchaEnabled.value = response.captchaEnabled ?? true
  register.value = response.registerEnabled ?? false
  if (captchaEnabled.value) {
    codeUrl.value = `data:image/gif;base64,${response.img}`
    loginForm.value.uuid = response.uuid
  }
}

function getCookie() {
  const username = Cookies.get('username')
  const password = Cookies.get('password')
  const rememberMe = Cookies.get('rememberMe')
  loginForm.value = {
    ...loginForm.value,
    username: username ?? loginForm.value.username,
    password: password === undefined ? loginForm.value.password : decrypt(password),
    rememberMe: rememberMe === undefined ? false : rememberMe === 'true',
  }
}

getCode()
getCookie()
</script>

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
