<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  ConfigProvider as AConfigProvider,
  Spin as ASpin
} from 'ant-design-vue'
import {
  AppstoreOutlined,
  BarChartOutlined,
  LogoutOutlined,
  MessageOutlined
} from '@ant-design/icons-vue'
import { identity, logout } from '../modules/identity/public.ts'
import { errorMessage } from '../shared/http.ts'
const route = useRoute()
const router = useRouter()
const leaving = ref(false)
const error = ref('')
const navigation = computed(() =>
  router
    .getRoutes()
    .filter((item) => item.meta.navigation && (!item.meta.admin || identity.value?.is_admin))
)
const theme = {
  token: {
    colorPrimary: '#046A82',
    colorBgLayout: '#F5F7F7',
    colorText: '#151616',
    colorTextSecondary: '#697070',
    colorBorder: '#E4EAEA',
    borderRadius: 8,
    controlHeight: 36,
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
  }
}
async function signOut(): Promise<void> {
  leaving.value = true
  try {
    await logout()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    leaving.value = false
    await router.replace('/login')
  }
}
</script>
<template>
  <AConfigProvider :theme="theme">
    <AAlert v-if="error" :message="error" type="error" closable @close="error = ''" />
    <RouterView v-if="route.meta.public" />
    <div v-else-if="identity" class="app-layout">
      <aside class="app-sidebar">
        <RouterLink to="/chat" class="app-brand"
          ><span class="brand-mark small-brand">衡</span
          ><span>智衡<span class="brand-subtitle">AI WORKSPACE</span></span></RouterLink
        >
        <div class="nav-label">工作空间</div>
        <nav class="app-navigation" aria-label="主导航">
          <RouterLink
            v-for="item in navigation"
            :key="item.path"
            :to="item.path"
            :aria-label="item.meta.title ?? item.path"
            active-class="active"
            ><MessageOutlined v-if="item.name === 'chat'" /><AppstoreOutlined
              v-else-if="item.name === 'models'"
            /><BarChartOutlined v-else /><span>{{ item.meta.title }}</span></RouterLink
          >
        </nav>
        <div class="sidebar-bottom">
          <div class="workspace-status"><span /> AI 工作空间</div>
          <div class="identity-card">
            <div class="user-avatar">{{ identity?.username.slice(0, 1).toUpperCase() }}</div>
            <div class="identity-text">
              <strong>{{ identity?.username }}</strong
              ><small>{{ identity?.is_admin ? '管理员' : '成员' }}</small>
            </div>
            <AButton type="text" :loading="leaving" aria-label="退出登录" @click="signOut"
              ><LogoutOutlined
            /></AButton>
          </div>
        </div>
      </aside>
      <main class="app-content">
        <RouterView :key="route.path" />
      </main>
    </div>
    <ASpin v-else class="page-spinner" tip="正在连接工作空间" />
  </AConfigProvider>
</template>
