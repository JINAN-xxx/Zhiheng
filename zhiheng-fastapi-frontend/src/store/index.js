import { createPinia } from 'pinia'
import { sessionLifecyclePlugin } from './sessionLifecycle.js'

const store = createPinia()
store.use(sessionLifecyclePlugin)

export default store
