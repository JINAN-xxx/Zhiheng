<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  ConfigProvider as AConfigProvider,
  Drawer as ADrawer,
  Spin as ASpin
} from 'ant-design-vue'
import { onBeforeUnmount, onMounted, watch, nextTick } from 'vue'
import { identity, logout } from '../modules/identity/public.ts'
import { ChatWorkspaceProvider } from '../modules/chat/public.ts'
import { WorkWorkspaceProvider } from '../modules/work/public.ts'
import { lightTheme } from '../shared/theme.ts'
import { errorMessage } from '../shared/http.ts'
import PlatformSidebar from './components/PlatformSidebar.vue'
import AiResourceShell from './components/AiResourceShell.vue'
import PlatformTopbar from './components/PlatformTopbar.vue'
const route = useRoute()
const router = useRouter()
const routeViewKey = computed(() => {
  if (route.matched.some((record) => record.name === 'admin')) return 'admin'
  const parameter = route.meta.reuseByParam
  return parameter ? `${String(route.name)}:${String(route.params[parameter] ?? '')}` : route.path
})
const leaving = ref(false)
const error = ref('')
const collapsed = ref(false)
const mobileOpen = ref(false)
const viewportWidth = ref(window.innerWidth)
const isMobile = computed(() => viewportWidth.value < 992)
const topbar = ref<InstanceType<typeof PlatformTopbar> | null>(null)
const drawerWidth = computed(() => Math.min(viewportWidth.value * 0.86, 300))
function resize(): void {
  viewportWidth.value = window.innerWidth
  if (!isMobile.value) mobileOpen.value = false
}
onMounted(() => window.addEventListener('resize', resize))
onBeforeUnmount(() => window.removeEventListener('resize', resize))
watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false
  }
)
async function afterDrawerChange(open: boolean): Promise<void> {
  if (!open) {
    await nextTick()
    topbar.value?.focusMenu()
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
  <AConfigProvider :theme="lightTheme">
    <AAlert v-if="error" :message="error" type="error" closable @close="error = ''" />
    <RouterView v-if="route.meta.public" />
    <ChatWorkspaceProvider v-else-if="identity">
      <WorkWorkspaceProvider>
        <div class="platform-shell" :class="{ 'is-collapsed': collapsed }">
          <div v-show="!isMobile" class="platform-shell__sidebar">
            <PlatformSidebar
              :collapsed="collapsed"
              show-collapse-control
              :leaving="leaving"
              @logout="signOut"
              @toggle-collapse="collapsed = !collapsed"
            />
          </div>
          <div class="platform-shell__main">
            <PlatformTopbar
              ref="topbar"
              :title="route.meta.title || '工作台'"
              :mobile="isMobile"
              :leaving="leaving"
              @toggle-sidebar="mobileOpen = true"
              @logout="signOut"
            />
            <main
              class="platform-page"
              :class="{
                'is-conversation': ['chat', 'work', 'assistant-chat'].includes(String(route.name))
              }"
            >
              <AiResourceShell
                v-if="
                  [
                    'models',
                    'credentials',
                    'usage',
                    'developer',
                    'skills',
                    'agents',
                    'agent-create',
                    'agent-editor'
                  ].includes(String(route.name))
                "
                inset
              >
                <RouterView :key="routeViewKey" />
              </AiResourceShell>
              <RouterView v-else :key="routeViewKey" />
            </main>
          </div>
          <ADrawer
            :open="isMobile && mobileOpen"
            placement="left"
            :width="drawerWidth"
            :closable="false"
            root-class-name="platform-mobile-drawer"
            aria-label="主导航"
            @close="mobileOpen = false"
            @after-open-change="afterDrawerChange"
            ><PlatformSidebar
              :collapsed="false"
              :leaving="leaving"
              @logout="signOut"
              @navigate="mobileOpen = false"
          /></ADrawer>
        </div>
      </WorkWorkspaceProvider>
    </ChatWorkspaceProvider>
    <ASpin v-else class="page-spinner" tip="正在连接工作空间" />
  </AConfigProvider>
</template>
<style scoped lang="scss">
.platform-shell {
  width: 100%;
  height: 100vh;
  display: flex;
  overflow: hidden;
  background: var(--workspace-bg);
}
.platform-shell__sidebar {
  width: var(--platform-sidebar-width);
  min-width: var(--platform-sidebar-width);
  flex: 0 0 var(--platform-sidebar-width);
  height: 100%;
  margin: 0;
  padding: 0;
  border-radius: 0;
  background: transparent;
  line-height: normal;
  transition:
    width 0.2s ease,
    min-width 0.2s ease,
    flex-basis 0.2s ease;
}
.platform-shell.is-collapsed .platform-shell__sidebar {
  width: var(--platform-sidebar-collapsed-width);
  min-width: var(--platform-sidebar-collapsed-width);
  flex-basis: var(--platform-sidebar-collapsed-width);
}
.platform-shell__main {
  min-width: 0;
  height: 100%;
  display: flex;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}
.platform-shell__desktop-topbar {
  height: var(--platform-topbar-height);
  min-height: var(--platform-topbar-height);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 0 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-container);
}
.platform-shell__desktop-account {
  width: max-content;
}
.platform-shell__workspace-back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  margin-right: auto;
  padding: 0 12px;
  border: 1px solid var(--gray-150);
  border-radius: var(--workspace-radius-sm);
  background: var(--bg-container);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}
.platform-shell__workspace-back:hover {
  border-color: var(--main-500);
  background: var(--main-30);
  color: var(--main-700);
}
.platform-page {
  overflow: auto;
  background: var(--workspace-bg);
}
@media screen and (max-width: 991px) {
  .platform-page {
    padding-bottom: max(60px, calc(env(safe-area-inset-bottom) + 40px));
    overscroll-behavior-y: none;
  }
}
@supports (-webkit-touch-callout: none) {
  @media screen and (max-width: 991px) {
    .platform-page {
      padding-bottom: max(17px, calc(env(safe-area-inset-bottom) + 10px));
    }
  }
}
.platform-page.is-conversation {
  padding-bottom: env(safe-area-inset-bottom, 0px);
}
</style>

<style lang="scss">
.platform-mobile-drawer .ant-drawer-body {
  padding: 0;
  overflow: hidden;
}
</style>
