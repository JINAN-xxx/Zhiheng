import { createApp } from 'vue'
import 'ant-design-vue/dist/reset.css'
import '../shared/styles/console-tokens.css'
import '../shared/styles/workspace.scss'
import '../shared/styles/zhiheng.scss'
import '../shared/styles.css'
import { clearIdentity } from '../modules/identity/public.ts'
import { http } from '../shared/http.ts'
import { initializeLightTheme } from '../shared/theme.ts'
import App from './App.vue'
import { router } from './router.ts'
initializeLightTheme()
http.setUnauthorizedHandler(() => {
  clearIdentity()
  void router.replace('/login')
})
const app = createApp(App)
app.use(router)
void router.isReady().then(
  () => app.mount('#app'),
  () => {
    const loading = document.querySelector('.startup-loading')
    if (loading) loading.textContent = '页面加载失败，请刷新重试'
  }
)
