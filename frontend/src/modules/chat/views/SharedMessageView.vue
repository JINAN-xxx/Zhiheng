<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Alert as AAlert, Button as AButton, Spin as ASpin } from 'ant-design-vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getSharedMessage } from '../api/messageActions.ts'
import type { SharedMessage } from '../api/messageActions.ts'
import AiMessage from '../components/AiMessage.vue'
const route = useRoute()
const shared = ref<SharedMessage | null>(null)
const loading = ref(false)
const error = ref('')
const scope = new RequestScope()
async function load(): Promise<void> {
  const context = scope.replace()
  shared.value = null
  error.value = ''
  loading.value = true
  try {
    const token = route.params.token
    if (typeof token !== 'string' || !token) throw new Error('分享链接无效、已过期或已撤销')
    const value = await getSharedMessage(token, context.signal)
    if (context.isCurrent()) shared.value = value
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason))
      error.value =
        reason instanceof ApiError && reason.status === 404
          ? '分享链接无效、已过期或已撤销'
          : errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
watch(
  () => route.params.token,
  () => {
    void load()
  },
  { immediate: true }
)
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <main class="shared-message-page">
    <article class="shared-message-card">
      <header>
        <h1>分享的回复</h1>
        <span v-if="shared" class="muted"
          >{{ new Date(shared.expires_at).toLocaleString() }} 到期</span
        >
      </header>
      <ASpin v-if="loading" />
      <template v-else-if="error"
        ><AAlert type="error" :message="error" show-icon /><AButton @click="load"
          >重新加载</AButton
        ></template
      >
      <template v-else-if="shared">
        <section v-if="shared.question !== null">
          <h2>提问</h2>
          <p class="shared-question">{{ shared.question }}</p>
        </section>
        <section>
          <h2>回复</h2>
          <AiMessage :content="shared.answer" />
        </section>
      </template>
    </article>
  </main>
</template>
<style scoped>
.shared-message-page {
  min-height: 100dvh;
  padding: 40px 20px;
  background: var(--bg-page);
  color: var(--color-text);
}
.shared-message-card {
  width: min(800px, 100%);
  margin: auto;
  padding: 28px;
  background: transparent;
  border: 0;
  border-radius: 0;
}
.shared-message-card header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 28px;
}
.shared-message-card h1 {
  margin: 0;
  font-size: 20px;
}
.shared-message-card h2 {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-secondary);
}
.shared-message-card section + section {
  margin-top: 28px;
}
.shared-question {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 480px) {
  .shared-message-page {
    padding: 12px;
  }
  .shared-message-card {
    padding: 18px;
  }
}
</style>
