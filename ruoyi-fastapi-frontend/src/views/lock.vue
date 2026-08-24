<template>
  <AuthShell
    class="lock-container auth-shell"
    title="工作区已锁定"
    description="验证当前账户密码后继续使用。"
  >
    <div class="lock-clock" aria-live="off">
      <strong>{{ currentTime }}</strong>
      <span>{{ currentDate }}</span>
    </div>

    <div class="lock-user">
      <a-avatar :size="72" :src="avatar" />
      <div>
        <strong>{{ userStore.nickName || userStore.name }}</strong>
        <span>请输入登录密码解锁</span>
      </div>
    </div>

    <a-form class="lock-form" @submit.prevent="handleUnlock">
      <a-input-password
        ref="passwordInput"
        v-model:value="password"
        autocomplete="current-password"
        placeholder="登录密码"
        size="large"
        @press-enter="handleUnlock"
      >
        <template #prefix><LockOutlined /></template>
      </a-input-password>
      <a-alert v-if="errorMsg" class="lock-error" :message="errorMsg" show-icon type="error" />
      <a-button block :loading="loading" size="large" type="primary" @click="handleUnlock">
        <template #icon><UnlockOutlined /></template>
        解锁工作区
      </a-button>
    </a-form>

    <a-button class="lock-logout" type="link" @click="goLogin">
      <template #icon><LogoutOutlined /></template>
      退出并重新登录
    </a-button>
  </AuthShell>
</template>

<script setup>
import { LockOutlined, LogoutOutlined, UnlockOutlined } from '@ant-design/icons-vue'
import { useRouter } from 'vue-router'

import { unlockScreen } from '@/api/login'
import defAva from '@/assets/images/profile.jpg'
import { AuthShell } from '@/components/Antd'
import useLockStore from '@/store/modules/lock'
import useUserStore from '@/store/modules/user'

const router = useRouter()
const userStore = useUserStore()
const lockStore = useLockStore()

const avatar = computed(() => userStore.avatar || defAva)
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')
const currentTime = ref('')
const currentDate = ref('')
const passwordInput = ref()
let timer

function updateClock() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  const days = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
  currentTime.value = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  currentDate.value = `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 ${days[now.getDay()]}`
}

async function handleUnlock() {
  if (loading.value) return
  if (!password.value) {
    errorMsg.value = '请输入密码'
    passwordInput.value?.focus()
    return
  }

  loading.value = true
  errorMsg.value = ''
  try {
    await unlockScreen(password.value)
    const lockPath = lockStore.lockPath
    lockStore.unlockScreen()
    await router.replace(lockPath)
  } catch (error) {
    errorMsg.value = error?.message ?? String(error)
    password.value = ''
    nextTick(() => passwordInput.value?.focus())
  } finally {
    loading.value = false
  }
}

async function goLogin() {
  lockStore.unlockScreen()
  await userStore.logOut()
  await router.push('/login')
}

onMounted(() => {
  updateClock()
  timer = window.setInterval(updateClock, 1000)
  nextTick(() => passwordInput.value?.focus())
})

onBeforeUnmount(() => window.clearInterval(timer))
</script>

<style scoped>
.lock-clock {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 20px;
  margin: -6px 0 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--gray-150);
}

.lock-clock strong {
  color: var(--color-text);
  font-family: var(--workspace-mono);
  font-size: 34px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.lock-clock span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.lock-user {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.lock-user > div {
  display: grid;
  gap: 4px;
}

.lock-user strong {
  color: var(--color-text);
  font-size: 16px;
}

.lock-user span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.lock-form {
  display: grid;
  gap: 14px;
}

.lock-error {
  margin-top: -4px;
}

.lock-logout {
  display: block;
  margin: 16px auto -8px;
}

@media (max-width: 400px) {
  .lock-clock {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }
}
</style>
