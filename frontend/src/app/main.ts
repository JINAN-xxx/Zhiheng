import { createApp, watch } from 'vue'
import 'ant-design-vue/dist/reset.css'
import '../shared/styles/console-tokens.css'
import '../shared/styles/workspace.scss'
import '../shared/styles/zhiheng.scss'
import '../shared/styles.css'
import { clearIdentity, identity, restoreIdentity } from '../modules/identity/public.ts'
import { http } from '../shared/http.ts'
import { initializeLightTheme } from '../shared/theme.ts'
import App from './App.vue'
import { router } from './router.ts'
initializeLightTheme()
http.setUnauthorizedHandler(() => {
  clearIdentity()
  if (!router.currentRoute.value.meta.public) void router.replace('/login')
})
let lastDeniedRefresh = 0
http.setForbiddenHandler(() => {
  if (!identity.value || Date.now() - lastDeniedRefresh < 1000) return
  lastDeniedRefresh = Date.now()
  void restoreIdentity(true).catch(() => undefined)
})
window.addEventListener('focus', () => {
  if (identity.value) void restoreIdentity().catch(() => undefined)
})
watch(identity, (current, previous) => {
  if (
    current &&
    previous &&
    (current.is_admin !== previous.is_admin ||
      JSON.stringify(current.permissions) !== JSON.stringify(previous.permissions))
  ) {
    void router.replace('/chat')
  }
})
const app = createApp(App)
app.use(router)
void router.isReady().then(
  () => app.mount('#app'),
  () => {
    const loading = document.querySelector('#app > .startup-loading')
    if (loading) loading.textContent = '页面加载失败，请刷新重试'
    window.dispatchEvent(new Event('platform:startup-failure'))
  }
)
