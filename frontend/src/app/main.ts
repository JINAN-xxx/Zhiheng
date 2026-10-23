import { createApp } from 'vue'
import 'ant-design-vue/dist/reset.css'
import '../shared/styles/yuxi-tokens.css'
import '../shared/styles/yuxi-tokens.dark.css'
import '../shared/styles/workspace.scss'
import '../shared/styles/zhiheng.scss'
import '../shared/styles.css'
import { clearIdentity } from '../modules/identity/public.ts'
import { http } from '../shared/http.ts'
import App from './App.vue'
import { router } from './router.ts'
http.setUnauthorizedHandler(() => {
  clearIdentity()
  void router.replace('/login')
})
const app = createApp(App)
app.use(router)
app.mount('#app')
