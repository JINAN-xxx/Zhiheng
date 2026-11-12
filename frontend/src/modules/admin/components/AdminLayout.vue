<script setup lang="ts">
import { nextTick, computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { Alert as AAlert, Spin as ASpin } from 'ant-design-vue'
import {
  TeamOutlined,
  SafetyCertificateOutlined,
  NotificationOutlined,
  HddOutlined,
  DashboardOutlined
} from '@ant-design/icons-vue'
import { identity, restoreIdentity } from '../../identity/public.ts'
import {
  adminNavigation,
  adminEntryLabel,
  hasAdminPermission,
  visibleAdminGroups
} from '../access.ts'
import { adminReadCacheKey, createAdminReadCache } from '../readCache.ts'
import { errorMessage } from '../../../shared/http.ts'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
const primaryNavigation = ref<HTMLElement | null>(null)
const switchError = ref('')
const route = useRoute(),
  router = useRouter()
const authority = computed(() =>
  JSON.stringify([
    identity.value?.id,
    identity.value?.is_admin,
    [...(identity.value?.permissions ?? [])].sort()
  ])
)
const readCache = createAdminReadCache(authority.value)
provide(adminReadCacheKey, readCache)
watch(authority, (value) => readCache.reset(value))
const pendingPath = ref<string | null>(null),
  slowSwitch = ref(false)
let switchTimer: ReturnType<typeof setTimeout> | null = null,
  switchVersion = 0,
  alive = true
watch(pendingPath, (value) => {
  if (switchTimer) clearTimeout(switchTimer)
  slowSwitch.value = false
  if (value)
    switchTimer = setTimeout(() => {
      slowSwitch.value = !!pendingPath.value
    }, 140)
})
const icons = {
  users: TeamOutlined,
  roles: SafetyCertificateOutlined,
  notices: NotificationOutlined,
  storage: HddOutlined,
  operations: DashboardOutlined
}
const canonicalPath = computed(() => {
  const record = route.matched.at(-1)
  return record?.aliasOf?.path || record?.path || route.path
})
const groups = computed(() => visibleAdminGroups(identity.value))
const displayedPath = computed(() => pendingPath.value ?? canonicalPath.value)
const activeGroup = computed(() =>
  groups.value.find((group) => group.entries.some((entry) => entry.path === displayedPath.value))
)
const permitted = computed(() =>
  adminNavigation.some(
    (entry) => entry.path === canonicalPath.value && hasAdminPermission(identity.value, entry.area)
  )
)
const viewKey = computed(() => `${canonicalPath.value}:${readCache.generation.value}`)
const indicator = ref({ left: 0, top: 0, width: 0, height: 0, visible: false })
const indicatorStyle = computed(() => ({
  transform: `translateX(${indicator.value.left}px)`,
  top: `${indicator.value.top}px`,
  width: `${indicator.value.width}px`,
  height: `${indicator.value.height}px`,
  opacity: indicator.value.visible ? 1 : 0
}))
async function positionIndicator(): Promise<void> {
  await nextTick()
  const container = primaryNavigation.value,
    active = container?.querySelector<HTMLElement>('a.is-active')
  if (!container || !active) {
    indicator.value.visible = false
    return
  }
  indicator.value = {
    left: active.offsetLeft,
    top: active.offsetTop,
    width: active.offsetWidth,
    height: active.offsetHeight,
    visible: true
  }
  const box = container.getBoundingClientRect(),
    target = active.getBoundingClientRect()
  const shift =
    target.left < box.left + 8
      ? target.left - box.left - 8
      : target.right > box.right - 8
        ? target.right - box.right + 8
        : 0
  if (shift)
    container.scrollTo({
      left: container.scrollLeft + shift,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    })
}
watch([displayedPath, groups], () => void positionIndicator(), { flush: 'post' })
let resize: ResizeObserver | null = null
const warmed = new Set<string>()
function warmPage(path: string): void {
  if (warmed.has(path) || !adminNavigation.some((entry) => entry.path === path)) return
  const component = router.resolve(path).matched.at(-1)?.components?.default
  if (
    typeof component !== 'function' ||
    'displayName' in component ||
    'props' in component ||
    '__vccOpts' in component
  )
    return
  warmed.add(path)
  void Promise.resolve((component as () => Promise<unknown>)()).catch(() => warmed.delete(path))
}
async function navigate(event: MouseEvent, path: string): Promise<void> {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  )
    return
  event.preventDefault()
  if (path === canonicalPath.value && !pendingPath.value) return
  const version = ++switchVersion
  pendingPath.value = path
  switchError.value = ''
  warmPage(path)
  try {
    await router.push(path)
  } catch (error) {
    if (alive && version === switchVersion) switchError.value = errorMessage(error)
  } finally {
    if (alive && version === switchVersion) pendingPath.value = null
  }
}
onBeforeRouteUpdate(async (to, from) => {
  if (to.path === from.path || from.path === '/admin' || from.path === '/admin/') return true
  if (!pendingPath.value) pendingPath.value = to.path
  try {
    const user = await restoreIdentity(true)
    if (!user) return '/login'
    const record = to.matched.at(-1),
      path = record?.aliasOf?.path || record?.path || to.path
    const entry = adminNavigation.find((item) => item.path === path)
    if (entry && !hasAdminPermission(user, entry.area)) {
      if (pendingPath.value === to.path) switchError.value = '当前账号无权访问此页面'
      return false
    }
    return true
  } catch (error) {
    if (pendingPath.value === to.path) switchError.value = errorMessage(error)
    return false
  }
})
const stopAfterEach = router.afterEach((to) => {
  if (pendingPath.value === to.path) pendingPath.value = null
})
watch(
  [() => route.path, groups],
  () => {
    if ((route.path === '/admin' || route.path === '/admin/') && groups.value[0])
      void router.replace(groups.value[0].path)
  },
  { immediate: true }
)
onMounted(() => {
  resize = new ResizeObserver(() => void positionIndicator())
  watch(
    primaryNavigation,
    (value) => {
      if (value) resize?.observe(value)
    },
    { immediate: true }
  )
})
onBeforeUnmount(() => {
  alive = false
  switchVersion += 1
  readCache.dispose()
  resize?.disconnect()
  stopAfterEach()
  if (switchTimer) clearTimeout(switchTimer)
})
</script>
<template>
  <div class="admin-console">
    <nav ref="primaryNavigation" class="admin-navigation" aria-label="后台管理导航">
      <span class="admin-navigation-indicator" :style="indicatorStyle" aria-hidden="true" />
      <RouterLink v-for="group in groups" :key="group.id" v-slot="{ href }" :to="group.path" custom>
        <a
          :href="href"
          :class="{ 'is-active': activeGroup?.id === group.id }"
          :aria-current="activeGroup?.id === group.id ? 'page' : undefined"
          @mouseenter="warmPage(group.path)"
          @focus="warmPage(group.path)"
          @click="navigate($event, group.path)"
        >
          <component :is="icons[group.id as keyof typeof icons]" aria-hidden="true" /><span>{{
            group.label
          }}</span>
        </a>
      </RouterLink>
    </nav>
    <div class="admin-section-slot" :class="{ 'is-expanded': activeGroup?.tabs }">
      <nav
        v-if="activeGroup?.tabs"
        class="admin-section-navigation"
        :aria-label="activeGroup.label + '子页面'"
      >
        <RouterLink
          v-for="entry in activeGroup.entries"
          :key="entry.path"
          v-slot="{ href }"
          :to="entry.path"
          custom
        >
          <a
            :href="href"
            :class="{ 'is-active': displayedPath === entry.path }"
            :aria-current="displayedPath === entry.path ? 'page' : undefined"
            @mouseenter="warmPage(entry.path)"
            @focus="warmPage(entry.path)"
            @click="navigate($event, entry.path)"
            >{{ adminEntryLabel(entry) }}</a
          >
        </RouterLink>
      </nav>
    </div>
    <div class="admin-content" :class="{ 'is-switching': slowSwitch }" :aria-busy="!!pendingPath">
      <div v-if="slowSwitch" class="admin-switch-progress" role="status">
        <ASpin size="small" /><span>正在切换</span>
      </div>
      <AAlert
        v-if="switchError"
        class="admin-navigation-error"
        :message="switchError"
        type="warning"
        show-icon
      />
      <WorkspaceState
        v-if="['/admin', '/admin/'].includes(route.path) && groups.length"
        kind="loading"
      />
      <RouterView v-else-if="permitted" v-slot="{ Component }"
        ><div :key="viewKey" class="admin-content-page" :inert="!!pendingPath">
          <component :is="Component" /></div
      ></RouterView>
      <WorkspaceState
        v-else
        kind="empty"
        title="暂无可访问的管理页面"
        message="当前账号没有可用的管理入口。"
      />
    </div>
  </div>
</template>
<style lang="scss">
.admin-console {
  width: min(100%, 1560px);
  min-width: 0;
  min-height: 100%;
  margin: 0 auto;
  color: var(--color-text);
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}
.admin-navigation {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 18px 32px 8px;
  overflow-x: auto;
  scrollbar-width: thin;
}
.admin-navigation a {
  position: relative;
  z-index: 1;
  transition: color 140ms ease;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex: 0 0 auto;
  padding: 9px 14px;
  color: var(--color-text-secondary);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
}
.admin-navigation a:hover {
  color: var(--main-700);
  background: var(--gray-25);
}
.admin-navigation a.is-active:hover {
  background: transparent;
}
.admin-navigation a.is-active {
  color: var(--main-700);
}
.admin-navigation a:focus-visible,
.admin-section-navigation a:focus-visible {
  outline: 2px solid var(--main-300);
  outline-offset: 2px;
}
.admin-section-navigation {
  display: flex;
  gap: 22px;
  padding: 6px 46px 0;
  overflow-x: auto;
}
.admin-section-navigation a {
  line-height: 18px;
  font-weight: 500;
  transition: color 140ms ease;
  position: relative;
  flex: 0 0 auto;
  padding: 8px 0 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
  text-decoration: none;
  white-space: nowrap;
}
.admin-section-navigation a.is-active {
  color: var(--main-700);
  font-weight: 500;
}
.admin-section-navigation a.is-active::after {
  position: absolute;
  right: 0;
  bottom: 2px;
  left: 0;
  height: 2px;
  background: var(--main-600);
  border-radius: 2px;
  content: '';
}
.admin-content {
  min-width: 0;
}
.admin-navigation-error {
  margin: 12px 32px;
}
.admin-page {
  min-width: 0;
  padding-bottom: 28px;
}
.admin-console .admin-page > .workspace-toolbar {
  min-height: 72px;
  padding: 12px 32px 16px;
}
.admin-page__body {
  display: grid;
  gap: 16px;
  min-width: 0;
  padding: 0 32px;
}
.admin-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  min-height: 36px;
}
.admin-tools .ant-input-search {
  width: min(320px, 100%);
}
.admin-total {
  margin-left: auto;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}
.admin-page .ant-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}
.admin-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.admin-actions .ant-btn {
  padding: 0 6px;
  font-size: 13px;
}
.admin-primary {
  display: grid;
  gap: 4px;
}
.admin-primary strong {
  font-weight: 500;
}
.admin-primary small,
.admin-muted {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.admin-code {
  font-family: var(--workspace-font-mono);
  overflow-wrap: anywhere;
}
.admin-text {
  max-width: 480px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.admin-filter {
  min-width: 130px;
}
@media (max-width: 767px) {
  .admin-navigation {
    gap: 3px;
    padding: 12px 16px 6px;
  }
  .admin-navigation a {
    gap: 6px;
    padding: 8px 11px;
  }
  .admin-section-navigation {
    padding: 4px 27px 0;
    gap: 20px;
  }
  .admin-console .admin-page > .workspace-toolbar {
    min-height: auto;
    padding: 16px 16px 14px;
  }
  .admin-page__body {
    padding: 0 16px;
    gap: 14px;
  }
  .admin-navigation-error {
    margin: 10px 16px;
  }
  .admin-tools {
    gap: 10px;
  }
}

.admin-navigation-indicator {
  position: absolute;
  left: 0;
  z-index: 0;
  pointer-events: none;
  background: var(--main-50);
  border-radius: 8px;
  transition:
    transform 160ms cubic-bezier(0.2, 0.7, 0.2, 1),
    width 160ms cubic-bezier(0.2, 0.7, 0.2, 1),
    height 160ms ease,
    opacity 100ms ease;
}
.admin-section-slot {
  height: 0;
  overflow: hidden;
  transition: height 150ms ease;
}
.admin-section-slot.is-expanded {
  height: 44px;
}
.admin-content {
  position: relative;
}
.admin-content.is-switching > .admin-content-page {
  opacity: 0.72;
  transition: opacity 120ms ease;
}
.admin-content-page {
  min-width: 0;
  animation: admin-page-arrive 140ms ease-out;
}
.admin-switch-progress {
  position: absolute;
  right: 32px;
  top: 6px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
@keyframes admin-page-arrive {
  from {
    opacity: 0.55;
    transform: translateY(3px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
@media (max-width: 767px) {
  .admin-section-slot.is-expanded {
    height: 40px;
  }
  .admin-switch-progress {
    right: 16px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .admin-navigation-indicator,
  .admin-section-slot,
  .admin-navigation a,
  .admin-section-navigation a,
  .admin-content.is-switching > .admin-content-page {
    transition: none;
  }
  .admin-content-page {
    animation: none;
  }
}
</style>
