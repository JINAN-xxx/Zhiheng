<template>
  <nav
    id="tags-view-container"
    class="tags-view-container"
    :class="{
      'tags-view-container--card': tagsViewStyle === 'card',
      'tags-view-container--chrome': tagsViewStyle === 'chrome',
    }"
    aria-label="管理页面标签"
  >
    <button class="tags-icon-button" :disabled="!canScrollLeft" aria-label="向左滚动" @click="scrollLeft">
      <LeftOutlined />
    </button>

    <ScrollPane ref="scrollPaneRef" class="tags-view-wrapper" @scroll="handleScroll" @update-arrows="updateArrowState">
      <router-link
        v-for="tag in visitedViews"
        :key="tag.path"
        :data-path="tag.path"
        :class="{ active: isActive(tag), 'has-icon': tagsIcon }"
        :to="{ path: tag.path, query: tag.query, fullPath: tag.fullPath }"
        class="tags-view-item"
        :aria-current="isActive(tag) ? 'page' : undefined"
        @click.middle="!isAffix(tag) && closeSelectedTag(tag)"
        @contextmenu.prevent="openMenu(tag, $event)"
      >
        <svg-icon v-if="tagsIcon && tag.meta?.icon && tag.meta.icon !== '#'" :icon-class="tag.meta.icon" />
        <span>{{ tag.title }}</span>
        <button
          v-if="!isAffix(tag)"
          class="tags-close-button"
          type="button"
          :aria-label="`关闭 ${tag.title}`"
          @click.prevent.stop="closeSelectedTag(tag)"
        >
          <CloseOutlined />
        </button>
      </router-link>
    </ScrollPane>

    <button class="tags-icon-button" :disabled="!canScrollRight" aria-label="向右滚动" @click="scrollRight">
      <RightOutlined />
    </button>

    <a-dropdown :trigger="['click']" placement="bottomRight">
      <button class="tags-icon-button" aria-label="标签操作">
        <DownOutlined />
      </button>
      <template #overlay>
        <a-menu @click="handleDropdownClick">
          <a-menu-item v-if="!isAffix(selectedDropdownTag)" key="close"><CloseOutlined />关闭当前</a-menu-item>
          <a-menu-item key="closeOthers"><CloseCircleOutlined />关闭其他</a-menu-item>
          <a-menu-item key="closeLeft" :disabled="isFirstView()"><ArrowLeftOutlined />关闭左侧</a-menu-item>
          <a-menu-item key="closeRight" :disabled="isLastView()"><ArrowRightOutlined />关闭右侧</a-menu-item>
          <a-menu-item key="closeAll"><StopOutlined />全部关闭</a-menu-item>
          <a-menu-divider />
          <a-menu-item key="fullscreen"><FullscreenOutlined />全屏显示</a-menu-item>
        </a-menu>
      </template>
    </a-dropdown>

    <button class="tags-refresh-button" type="button" @click="refreshSelectedTag(selectedDropdownTag)">
      <ReloadOutlined />
      <span>刷新</span>
    </button>

    <ul v-show="visible" :style="{ left: `${left}px`, top: `${top}px` }" class="contextmenu">
      <li @click="refreshSelectedTag(selectedTag)"><ReloadOutlined />刷新页面</li>
      <li v-if="!isAffix(selectedTag)" @click="closeSelectedTag(selectedTag)"><CloseOutlined />关闭当前</li>
      <li @click="closeOthersTags"><CloseCircleOutlined />关闭其他</li>
      <li v-if="!isFirstView()" @click="closeLeftTags"><ArrowLeftOutlined />关闭左侧</li>
      <li v-if="!isLastView()" @click="closeRightTags"><ArrowRightOutlined />关闭右侧</li>
      <li @click="closeAllTags(selectedTag)"><StopOutlined />全部关闭</li>
    </ul>
  </nav>
</template>

<script setup>
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CloseCircleOutlined,
  CloseOutlined,
  DownOutlined,
  FullscreenOutlined,
  LeftOutlined,
  ReloadOutlined,
  RightOutlined,
  StopOutlined,
} from '@ant-design/icons-vue'

import usePermissionStore from '@/store/modules/permission'
import useSettingsStore from '@/store/modules/settings'
import useTagsViewStore from '@/store/modules/tagsView'
import { getNormalPath } from '@/utils/ruoyi'
import ScrollPane from './ScrollPane.vue'

const visible = ref(false)
const top = ref(0)
const left = ref(0)
const selectedTag = ref({})
const affixTags = ref([])
const scrollPaneRef = ref()
const canScrollLeft = ref(false)
const canScrollRight = ref(false)

const { proxy } = getCurrentInstance()
const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()
const tagsViewStore = useTagsViewStore()

const visitedViews = computed(() => tagsViewStore.visitedViews)
const routes = computed(() => usePermissionStore().routes)
const tagsIcon = computed(() => settingsStore.tagsIcon)
const tagsViewPersist = computed(() => settingsStore.tagsViewPersist)
const tagsViewStyle = computed(() => settingsStore.tagsViewStyle)
const selectedDropdownTag = computed(() => visitedViews.value.find((view) => isActive(view)) || {})

watch(route, () => {
  addTags()
  moveToCurrentTag()
})
watch(visible, (value) => {
  if (value) document.body.addEventListener('click', closeMenu)
  else document.body.removeEventListener('click', closeMenu)
})
watch(visitedViews, updateArrowState)

onMounted(() => {
  initTags()
  addTags()
  window.addEventListener('resize', updateArrowState)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateArrowState)
  document.body.removeEventListener('click', closeMenu)
})

function isActive(view) {
  return view.path === route.path
}

function isAffix(tag) {
  return Boolean(tag?.meta?.affix)
}

function getSelectedTag() {
  return selectedTag.value?.fullPath ? selectedTag.value : selectedDropdownTag.value
}

function isFirstView() {
  const tag = getSelectedTag()
  const closableViews = visitedViews.value.filter((view) => !isAffix(view))
  return !tag.fullPath || closableViews[0]?.fullPath === tag.fullPath
}

function isLastView() {
  const tag = getSelectedTag()
  return !tag.fullPath || visitedViews.value.at(-1)?.fullPath === tag.fullPath
}

function filterAffixTags(routeList, basePath = '') {
  return routeList.flatMap((item) => {
    const result = []
    if (item.meta?.affix) {
      const path = getNormalPath(`${basePath}/${item.path}`)
      result.push({ fullPath: path, path, name: item.name, meta: { ...item.meta } })
    }
    if (item.children) result.push(...filterAffixTags(item.children, item.path))
    return result
  })
}

function initTags() {
  if (tagsViewPersist.value) tagsViewStore.loadPersistedViews()
  affixTags.value = filterAffixTags(routes.value)
  for (const tag of affixTags.value) {
    if (tag.name) tagsViewStore.addAffixView(tag)
  }
}

function addTags() {
  if (route.name) tagsViewStore.addView(route)
}

function moveToCurrentTag() {
  nextTick(() => {
    const current = visitedViews.value.find((view) => view.path === route.path)
    if (!current) return
    scrollPaneRef.value?.moveToTarget(current)
    if (current.fullPath !== route.fullPath) tagsViewStore.updateVisitedView(route)
  })
}

function scrollLeft() {
  if (canScrollLeft.value) scrollPaneRef.value?.scrollToStart()
}

function scrollRight() {
  if (canScrollRight.value) scrollPaneRef.value?.scrollToEnd()
}

function updateArrowState() {
  nextTick(() => {
    const state = scrollPaneRef.value?.getScrollState()
    canScrollLeft.value = Boolean(state?.canLeft)
    canScrollRight.value = Boolean(state?.canRight)
  })
}

function toggleFullscreen() {
  if (!document.fullscreenElement) document.querySelector('.app-main')?.requestFullscreen()
  else document.exitFullscreen()
}

function handleDropdownClick({ key }) {
  handleDropdownCommand(key)
}

function handleDropdownCommand(command) {
  const tag = selectedDropdownTag.value
  selectedTag.value = tag
  const handlers = {
    refresh: () => refreshSelectedTag(tag),
    fullscreen: toggleFullscreen,
    close: () => closeSelectedTag(tag),
    closeOthers: closeOthersTags,
    closeLeft: closeLeftTags,
    closeRight: closeRightTags,
    closeAll: () => closeAllTags(tag),
  }
  handlers[command]?.()
}

function refreshSelectedTag(view) {
  if (!view?.fullPath) return
  proxy.$tab.refreshPage(view)
  if (route.meta.link) tagsViewStore.delIframeView(route)
}

function closeSelectedTag(view) {
  if (!view?.fullPath) return
  proxy.$tab.closePage(view).then(({ visitedViews: remainingViews }) => {
    if (isActive(view)) toLastView(remainingViews, view)
  })
}

function closeRightTags() {
  proxy.$tab.closeRightPage(getSelectedTag()).then((remainingViews) => {
    if (!remainingViews.find((view) => view.fullPath === route.fullPath)) toLastView(remainingViews)
  })
}

function closeLeftTags() {
  proxy.$tab.closeLeftPage(getSelectedTag()).then((remainingViews) => {
    if (!remainingViews.find((view) => view.fullPath === route.fullPath)) toLastView(remainingViews)
  })
}

function closeOthersTags() {
  const tag = getSelectedTag()
  router.push(tag).catch(() => {})
  proxy.$tab.closeOtherPage(tag).then(moveToCurrentTag)
}

function closeAllTags(view) {
  proxy.$tab.closeAllPage().then(({ visitedViews: remainingViews }) => {
    if (!affixTags.value.some((tag) => tag.path === route.path)) toLastView(remainingViews, view)
  })
}

function toLastView(remainingViews, view) {
  const latestView = remainingViews.at(-1)
  if (latestView) router.push(latestView.fullPath)
  else if (view?.name === 'Dashboard') router.replace({ path: `/redirect${view.fullPath}` })
  else router.push('/')
}

function openMenu(tag, event) {
  left.value = event.clientX
  top.value = event.clientY
  visible.value = true
  selectedTag.value = tag
}

function closeMenu() {
  visible.value = false
}

function handleScroll() {
  closeMenu()
  updateArrowState()
}
</script>

<style scoped>
.tags-view-container {
  position: relative;
  display: flex;
  width: 100%;
  height: 35px;
  min-height: 35px;
  align-items: center;
  overflow: hidden;
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-container);
}

.tags-view-wrapper {
  min-width: 0;
  flex: 1;
}

.tags-view-item {
  display: inline-flex;
  height: 28px;
  align-items: center;
  gap: 5px;
  margin: 3px 0 3px 5px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: var(--workspace-radius-sm);
  color: var(--color-text-secondary);
  font-size: 12px;
  text-decoration: none;
}

.tags-view-item:hover {
  border-color: var(--gray-150);
  background: var(--gray-50);
  color: var(--color-text);
}

.tags-view-container--card .tags-view-item.active {
  border-color: var(--main-50);
  background: var(--main-50);
  color: var(--main-700);
  font-weight: 600;
}

.tags-view-container--chrome .tags-view-item.active {
  border-bottom: 2px solid var(--main-700);
  background: var(--bg-container);
  color: var(--main-700);
  font-weight: 600;
}

.tags-icon-button,
.tags-refresh-button,
.tags-close-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.tags-icon-button {
  width: 32px;
  height: 34px;
  flex: 0 0 32px;
  border-inline-start: 1px solid var(--gray-150);
}

.tags-icon-button:disabled {
  color: var(--color-text-tertiary);
  cursor: not-allowed;
  opacity: 0.45;
}

.tags-refresh-button {
  height: 34px;
  gap: 5px;
  padding: 0 10px;
  border-inline-start: 1px solid var(--gray-150);
  font-size: 12px;
}

.tags-close-button {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 10px;
}

.tags-close-button:hover {
  background: var(--gray-150);
  color: var(--color-text);
}

.contextmenu {
  position: fixed;
  z-index: 3000;
  min-width: 140px;
  margin: 0;
  padding: 5px;
  border: 1px solid var(--gray-150);
  border-radius: var(--workspace-radius-md);
  background: var(--bg-elevated);
  box-shadow: var(--workspace-shadow-float);
  color: var(--color-text);
  font-size: 12px;
  list-style: none;
}

.contextmenu li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--workspace-radius-sm);
  cursor: pointer;
}

.contextmenu li:hover {
  background: var(--gray-50);
}

@media (max-width: 760px) {
  .tags-refresh-button span {
    display: none;
  }

  .tags-refresh-button {
    width: 32px;
    padding: 0;
  }
}
</style>
