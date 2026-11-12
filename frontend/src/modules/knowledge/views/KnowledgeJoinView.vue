<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert as AAlert, Button as AButton, Spin as ASpin } from 'ant-design-vue'
import { BookOutlined } from '@ant-design/icons-vue'
import BrandMark from '../../../shared/components/BrandMark.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity, restoreIdentity } from '../../identity/public.ts'
import { previewKnowledgeInvitation, acceptKnowledgeInvitation } from '../api/index.ts'
import type { KnowledgeInvitationPreview } from '../api/index.ts'
import { formatTime } from '../presentation.ts'
const route = useRoute(),
  router = useRouter(),
  read = new RequestScope(),
  write = new RequestScope()
const token = ref(''),
  preview = ref<KnowledgeInvitationPreview | null>(null),
  authLoading = ref(false),
  loading = ref(false),
  accepting = ref(false),
  error = ref('')
const pendingKey = 'zhiheng:pending-knowledge-invitation'
const signedIn = computed(() => !!identity.value)
function capture() {
  let value = route.hash.slice(1)
  try {
    if (!value) value = sessionStorage.getItem(pendingKey) ?? ''
  } catch {
    /* The original fragment remains usable. */
  }
  if (/^[A-Za-z0-9_-]{43}$/.test(value)) {
    token.value = value
    try {
      sessionStorage.setItem(pendingKey, value)
    } catch {
      /* Login can be followed by reopening the original link. */
    }
  } else {
    token.value = ''
    error.value = '邀请链接无效，请向所有者获取新的邀请'
  }
}
async function load() {
  const ticket = read.replace(),
    user = identity.value,
    value = token.value
  preview.value = null
  error.value = ''
  if (!value) {
    error.value = '邀请链接无效，请向所有者获取新的邀请'
    return
  }
  if (!user) return
  loading.value = true
  try {
    const result = await previewKnowledgeInvitation(value, ticket.signal)
    if (ticket.isCurrent() && identity.value === user) preview.value = result
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function resolveIdentity() {
  authLoading.value = true
  try {
    await restoreIdentity()
  } catch (reason) {
    if (!isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    authLoading.value = false
  }
}
function login() {
  void router.push({ path: '/login', query: { redirect: '/knowledge/join' } })
}
function goHome() {
  clear()
  void router.push('/knowledge')
}
function clear() {
  try {
    sessionStorage.removeItem(pendingKey)
  } catch {
    /* Optional browser storage. */
  }
}
async function enter() {
  if (preview.value) {
    clear()
    await router.replace(`/knowledge/${encodeURIComponent(preview.value.base_id)}/documents`)
  }
}
async function accept() {
  if (!preview.value || !identity.value || accepting.value) return
  if (preview.value.already_member) {
    await enter()
    return
  }
  const ticket = write.replace(),
    user = identity.value,
    value = token.value
  accepting.value = true
  error.value = ''
  try {
    const result = await acceptKnowledgeInvitation(value, ticket.signal)
    if (!ticket.isCurrent() || identity.value !== user) return
    clear()
    await router.replace(`/knowledge/${encodeURIComponent(result.base_id)}/documents`)
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) accepting.value = false
  }
}
watch(
  () => route.hash,
  () => {
    write.replace()
    accepting.value = false
    capture()
    void load()
  },
  { immediate: true }
)
watch(
  () => identity.value?.id,
  () => {
    read.replace()
    write.replace()
    preview.value = null
    accepting.value = false
    void load()
  }
)
onMounted(() => {
  if (!identity.value) void resolveIdentity()
})
onBeforeUnmount(() => {
  read.dispose()
  write.dispose()
  token.value = ''
  preview.value = null
})
</script>
<template>
  <main class="knowledge-invitation-page">
    <section class="knowledge-invitation-card">
      <div class="invitation-brand"><BrandMark /></div>
      <span class="invitation-icon"><BookOutlined /></span>
      <h1>知识库共享邀请</h1>
      <ASpin v-if="authLoading || loading" tip="正在核对邀请" />
      <template v-else>
        <AAlert v-if="error" type="error" :message="error" show-icon />
        <template v-if="preview">
          <h2>{{ preview.base_name }}</h2>
          <p>{{ preview.owner_name }} 邀请你使用这个知识库。</p>
          <p>接受后可检索、查看和下载库内全部资料，权限为只读。</p>
          <p class="invitation-expiry">有效至 {{ formatTime(preview.expires_at) }}</p>
          <p class="invitation-account">当前账号：{{ identity?.username }}</p>
          <AButton type="primary" block :loading="accepting" @click="accept">{{
            preview.already_member ? '进入知识库' : '接受邀请并进入'
          }}</AButton>
        </template>
        <template v-else-if="!signedIn && token"
          ><p>登录后查看邀请并确认加入，链接不会开放匿名访问。</p>
          <AButton type="primary" block @click="login">登录后继续</AButton></template
        >
        <AButton v-else-if="token" block @click="load">重新核对</AButton>
      </template>
      <AButton type="link" class="invitation-home" @click="goHome">返回知识库</AButton>
    </section>
  </main>
</template>
<style scoped>
.knowledge-invitation-page {
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 24px;
  background: var(--gray-25);
  box-sizing: border-box;
}
.knowledge-invitation-card {
  box-sizing: border-box;
  width: min(100%, 460px);
  padding: 32px;
  border: 1px solid var(--gray-150);
  border-radius: 14px;
  background: var(--bg-container);
}
.invitation-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
  font-weight: 600;
  margin-bottom: 28px;
}
.invitation-brand :deep(svg) {
  width: 26px;
  height: 26px;
}
.invitation-icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--main-30);
  color: var(--main-color);
  font-size: 24px;
}
h1 {
  font-size: 20px;
  margin: 16px 0 24px;
}
h2 {
  font-size: 17px;
  margin: 20px 0 8px;
}
p {
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text-secondary);
}
.invitation-expiry,
.invitation-account {
  font-size: 12px;
}
.invitation-home {
  display: block;
  margin: 18px auto 0;
}
</style>
