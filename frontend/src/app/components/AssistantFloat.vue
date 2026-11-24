<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  ArrowRightOutlined,
  CloseOutlined,
  HomeOutlined,
  MessageOutlined,
  MinusOutlined,
  RobotOutlined,
  LeftOutlined,
  RightOutlined
} from '@ant-design/icons-vue'
import { canUseCapability, canUseModule, identity } from '../../modules/identity/public.ts'
import XiaohengCharacter from './XiaohengCharacter.vue'

const props = defineProps<{ conversation: boolean; suspended: boolean }>()
const route = useRoute()
const panelId = useId()
const root = ref<HTMLElement | null>(null)
const launcher = ref<HTMLButtonElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const opened = ref(false)
const docked = ref(false)
const dragging = ref(false)
// Store ratios in the available area so a saved desktop position also fits a phone.
const position = ref({ x: 1, y: 1 })
const viewport = ref({ left: 0, top: 0, width: window.innerWidth, height: window.innerHeight })
const safeArea = ref({ left: 0, top: 0, right: 0, bottom: 0 })
const inset = computed(() => ({
  left: Math.max(12, safeArea.value.left),
  top: Math.max(12, safeArea.value.top),
  right: Math.max(12, safeArea.value.right),
  bottom: Math.max(12, safeArea.value.bottom)
}))
const panelHeight = ref(260)
const mobile = computed(() => viewport.value.width < 992)
const size = computed(() =>
  docked.value
    ? { width: 28, height: 52 }
    : mobile.value
      ? { width: 60, height: 68 }
      : { width: 78, height: 88 }
)
const onLeft = computed(() => position.value.x < 0.5)
const available = computed(() => canUseModule('agents') || canUseCapability('chat', 'use'))
const entries = computed(() => [
  ...(canUseModule('agents')
    ? [{ path: '/agents', label: '打开智能助手', icon: RobotOutlined }]
    : []),
  ...(canUseCapability('chat', 'use')
    ? [{ path: '/chat', label: '打开对话体验', icon: MessageOutlined }]
    : []),
  ...(canUseModule('overview')
    ? [{ path: '/overview', label: '返回我的工作台', icon: HomeOutlined }]
    : [])
])
const preferenceKey = computed(() =>
  identity.value ? `zhiheng:assistant-float:${encodeURIComponent(identity.value.id)}` : null
)
const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), Math.max(min, max))
const bounds = computed(() => {
  const v = viewport.value
  // Keep the character above the conversation composer, including on a narrow screen.
  const bottom = props.conversation ? Math.min(240, v.height * 0.36) : 24
  const left = v.left + safeArea.value.left
  const top = v.top + inset.value.top
  return {
    left,
    top,
    right: Math.max(left, v.left + v.width - safeArea.value.right - size.value.width),
    bottom: Math.max(top, v.top + v.height - inset.value.bottom - bottom - size.value.height)
  }
})
const anchor = computed(() => {
  const b = bounds.value
  return {
    // Away from a drag, always rest against the nearest safe screen edge.
    left: dragging.value
      ? b.left + position.value.x * (b.right - b.left)
      : onLeft.value
        ? b.left
        : b.right,
    top: b.top + position.value.y * (b.bottom - b.top)
  }
})
const launcherStyle = computed(() => ({
  left: `${anchor.value.left}px`,
  top: `${anchor.value.top}px`,
  width: `${size.value.width}px`,
  height: `${size.value.height}px`
}))
const panelStyle = computed(() => {
  const v = viewport.value
  const width = Math.min(304, v.width - inset.value.left - inset.value.right)
  const maxHeight = Math.max(0, v.height - inset.value.top - inset.value.bottom)
  const height = Math.min(panelHeight.value, maxHeight)
  const above = anchor.value.top - height - 12
  return {
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
    left: `${clamp(anchor.value.left + size.value.width - width, v.left + inset.value.left, v.left + v.width - width - inset.value.right)}px`,
    top: `${above >= v.top + inset.value.top ? above : clamp(anchor.value.top + size.value.height + 12, v.top + inset.value.top, v.top + v.height - height - inset.value.bottom)}px`
  }
})
let pointer: { id: number; x: number; y: number; left: number; top: number } | null = null
let suppressClick = false
let observer: ResizeObserver | undefined

function savePreference(): void {
  if (!preferenceKey.value) return
  try {
    localStorage.setItem(
      preferenceKey.value,
      JSON.stringify({ ...position.value, docked: docked.value })
    )
  } catch {
    // Storage restrictions must not disable the launcher.
  }
}
watch(
  preferenceKey,
  (key) => {
    cancelDrag()
    opened.value = false
    position.value = { x: 1, y: 1 }
    docked.value = false
    if (!key) return
    try {
      const value: unknown = JSON.parse(localStorage.getItem(key) ?? 'null')
      if (
        value &&
        typeof value === 'object' &&
        'x' in value &&
        'y' in value &&
        'docked' in value &&
        typeof value.x === 'number' &&
        Number.isFinite(value.x) &&
        typeof value.y === 'number' &&
        Number.isFinite(value.y)
      ) {
        position.value = { x: value.x < 0.5 ? 0 : 1, y: clamp(value.y, 0, 1) }
        docked.value = value.docked === true
      }
    } catch {
      // Invalid or unavailable preferences fall back to the lower right corner.
    }
  },
  { immediate: true, flush: 'sync' }
)

function updateViewport(): void {
  const v = window.visualViewport
  viewport.value = {
    left: v?.offsetLeft ?? 0,
    top: v?.offsetTop ?? 0,
    width: v?.width ?? window.innerWidth,
    height: v?.height ?? window.innerHeight
  }
  if (root.value) {
    const css = getComputedStyle(root.value)
    safeArea.value = {
      left: parseFloat(css.paddingLeft),
      top: parseFloat(css.paddingTop),
      right: parseFloat(css.paddingRight),
      bottom: parseFloat(css.paddingBottom)
    }
  }
}
function cancelDrag(): void {
  if (pointer && launcher.value?.hasPointerCapture(pointer.id))
    launcher.value.releasePointerCapture(pointer.id)
  pointer = null
  position.value.x = onLeft.value ? 0 : 1
  dragging.value = false
}
function startDrag(event: PointerEvent): void {
  if (docked.value || !event.isPrimary || event.button !== 0) return
  suppressClick = false
  pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, ...anchor.value }
  launcher.value?.setPointerCapture(event.pointerId)
}
function moveDrag(event: PointerEvent): void {
  if (!pointer || pointer.id !== event.pointerId) return
  const dx = event.clientX - pointer.x
  const dy = event.clientY - pointer.y
  if (!dragging.value && Math.hypot(dx, dy) < 6) return
  dragging.value = true
  opened.value = false
  moveTo(pointer.left + dx, pointer.top + dy)
}
function moveTo(left: number, top: number): void {
  const b = bounds.value
  position.value = {
    x: b.right > b.left ? clamp((left - b.left) / (b.right - b.left), 0, 1) : 1,
    y: b.bottom > b.top ? clamp((top - b.top) / (b.bottom - b.top), 0, 1) : 1
  }
}
function keyboardMove(event: KeyboardEvent): void {
  if (
    docked.value ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
  )
    return
  event.preventDefault()
  const step = event.shiftKey ? 40 : 16
  opened.value = false
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    position.value.x = event.key === 'ArrowLeft' ? 0 : 1
  } else {
    moveTo(anchor.value.left, anchor.value.top + (event.key === 'ArrowUp' ? -step : step))
  }
  savePreference()
}
function finishDrag(event: PointerEvent): void {
  if (!pointer || pointer.id !== event.pointerId) return
  suppressClick = dragging.value
  cancelDrag()
  savePreference()
}
async function togglePanel(event: MouseEvent): Promise<void> {
  // A drag also emits click in browsers; keyboard activation must remain available.
  if (suppressClick && event.detail !== 0) {
    suppressClick = false
    return
  }
  suppressClick = false
  docked.value = false
  opened.value = !opened.value
  savePreference()
  if (opened.value) {
    await nextTick()
    panel.value?.querySelector<HTMLAnchorElement>('a')?.focus({ preventScroll: true })
  }
}
async function closePanel(dock = false): Promise<void> {
  opened.value = false
  if (dock) {
    docked.value = true
    savePreference()
  }
  await nextTick()
  launcher.value?.focus({ preventScroll: true })
}
function outsidePointer(event: PointerEvent): void {
  if (opened.value && event.target instanceof Node && !root.value?.contains(event.target))
    opened.value = false
}
watch(
  () => route.fullPath,
  () => {
    opened.value = false
  }
)
watch([available, () => props.suspended], () => {
  if (!available.value || props.suspended) {
    opened.value = false
    cancelDrag()
  }
})
watch(
  panel,
  (element) => {
    observer?.disconnect()
    if (element) observer?.observe(element)
  },
  { flush: 'post' }
)
onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    if (entry) panelHeight.value = entry.target.getBoundingClientRect().height
  })
  updateViewport()
  window.addEventListener('resize', updateViewport)
  window.visualViewport?.addEventListener('resize', updateViewport)
  window.visualViewport?.addEventListener('scroll', updateViewport)
  document.addEventListener('pointerdown', outsidePointer, true)
})
onBeforeUnmount(() => {
  cancelDrag()
  observer?.disconnect()
  window.removeEventListener('resize', updateViewport)
  window.visualViewport?.removeEventListener('resize', updateViewport)
  window.visualViewport?.removeEventListener('scroll', updateViewport)
  document.removeEventListener('pointerdown', outsidePointer, true)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="identity && available"
      v-show="!suspended"
      ref="root"
      class="assistant-float"
      @keydown.esc.stop.prevent="closePanel()"
    >
      <section
        v-if="opened"
        :id="panelId"
        ref="panel"
        class="assistant-float__panel"
        :style="panelStyle"
        :aria-labelledby="`${panelId}-title`"
        role="region"
      >
        <div class="assistant-float__controls">
          <button
            type="button"
            class="assistant-float__control"
            aria-label="贴边收起小助手"
            title="贴边收起"
            @click="closePanel(true)"
          >
            <MinusOutlined />
          </button>
          <button
            type="button"
            class="assistant-float__control"
            aria-label="关闭小助手浮窗"
            title="关闭浮窗"
            @click="closePanel()"
          >
            <CloseOutlined />
          </button>
        </div>
        <div class="assistant-float__content">
          <div class="assistant-float__greeting">
            <XiaohengCharacter expression="happy" />
            <div>
              <h3 :id="`${panelId}-title`">你好，我是小助手</h3>
              <p>想做点什么？我带你开始。</p>
            </div>
          </div>
          <nav aria-label="小助手快捷入口">
            <RouterLink
              v-for="(entry, index) in entries"
              :key="entry.path"
              :to="entry.path"
              :aria-label="entry.label"
              :class="['assistant-float__entry', { 'is-primary': index === 0 }]"
              @click="opened = false"
              ><component :is="entry.icon" aria-hidden="true" /><span>{{ entry.label }}</span
              ><ArrowRightOutlined v-if="index === 0" aria-hidden="true" /><RightOutlined
                v-else
                aria-hidden="true"
            /></RouterLink>
          </nav>
        </div>
      </section>
      <button
        ref="launcher"
        type="button"
        :class="[
          'assistant-float__launcher',
          {
            'is-docked': docked,
            'is-left': onLeft,
            'is-dragging': dragging,
            'is-peeking': !opened && !docked && !dragging
          }
        ]"
        :style="launcherStyle"
        :aria-label="docked ? '展开小助手' : '小助手'"
        :aria-expanded="opened"
        :aria-controls="opened ? panelId : undefined"
        :title="docked ? '展开小助手' : '点击打开小助手，拖动或用方向键调整位置'"
        @pointerdown="startDrag"
        @pointermove="moveDrag"
        @pointerup="finishDrag"
        @pointercancel="cancelDrag"
        @lostpointercapture="cancelDrag"
        @click="togglePanel"
        @keydown="keyboardMove"
      >
        <template v-if="docked"><RightOutlined v-if="onLeft" /><LeftOutlined v-else /></template>
        <span v-else class="assistant-float__character"
          ><XiaohengCharacter :animated="!dragging"
        /></span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.assistant-float {
  position: fixed;
  inset: 0;
  z-index: 90;
  overflow: clip;
  padding: env(safe-area-inset-top, 0px) env(safe-area-inset-right, 0px)
    env(safe-area-inset-bottom, 0px) env(safe-area-inset-left, 0px);
  pointer-events: none;
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.assistant-float__panel,
.assistant-float__launcher {
  position: absolute;
  pointer-events: auto;
}
.assistant-float__panel {
  overflow: auto;
  overscroll-behavior: contain;
  border: 1px solid #e5e1f3;
  border-radius: 10px;
  background: var(--bg-container);
  box-shadow:
    0 12px 42px #3d306a14,
    0 2px 8px #2e265a06;
}
.assistant-float__controls {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  padding: 5px 7px 0;
}
.assistant-float__control {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}
.assistant-float__control:hover {
  color: var(--main-color);
  background: var(--main-50);
}
.assistant-float__content {
  padding: 0 17px 15px;
}
.assistant-float__greeting {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 17px;
}
.assistant-float__greeting > svg {
  width: 47px;
  height: 53px;
  flex-shrink: 0;
}
.assistant-float__greeting h3 {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 600;
}
.assistant-float__greeting p {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.assistant-float__entry {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 39px;
  padding: 8px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-decoration: none;
  border-radius: 6px;
}
.assistant-float__entry > span:nth-child(2) {
  flex: 1;
}
.assistant-float__entry > span:last-child {
  font-size: 10px;
}
.assistant-float__entry:hover {
  color: var(--main-color);
}
.assistant-float__entry.is-primary {
  min-height: 38px;
  margin-bottom: 9px;
  padding: 9px 13px;
  background: var(--main-color);
  color: white;
}
.assistant-float__entry.is-primary:hover {
  background: #5145d8;
}
.assistant-float__launcher {
  display: grid;
  place-items: center;
  width: 78px;
  height: 88px;
  padding: 0;
  border: 0;
  border-radius: 10px;
  background: transparent;
  cursor: grab;
  transition: left 260ms cubic-bezier(0.2, 0.8, 0.3, 1);
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}
.assistant-float__launcher.is-dragging {
  cursor: grabbing;
  transition: none;
}
.assistant-float__character {
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
  transform-origin: 50% 85%;
  transition: transform 280ms cubic-bezier(0.2, 0.8, 0.3, 1);
}
.assistant-float__launcher.is-peeking .assistant-float__character {
  transform: translateX(28%) rotate(-7deg);
}
.assistant-float__launcher.is-peeking.is-left .assistant-float__character {
  transform: translateX(-28%) rotate(7deg);
}
.assistant-float__launcher.is-peeking:is(:hover, :focus-visible) .assistant-float__character {
  transform: none;
}
.assistant-float__launcher:not(.is-dragging):is(:hover, :focus-visible) :deep(.wave-arm) {
  transform-origin: 161px 181px;
  animation: assistant-wave 700ms ease-in-out 2;
}
@keyframes assistant-wave {
  25%,
  75% {
    transform: rotate(-14deg);
  }
  50% {
    transform: rotate(4deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .assistant-float__launcher,
  .assistant-float__character {
    transition: none;
  }
}
.assistant-float__launcher.is-docked {
  width: 28px;
  height: 52px;
  border: 1px solid #e5e1f3;
  border-right: 0;
  border-radius: 8px 0 0 8px;
  background: var(--main-50);
  color: var(--main-color);
  cursor: pointer;
}
.assistant-float__launcher.is-docked.is-left {
  border-right: 1px solid #e5e1f3;
  border-left: 0;
  border-radius: 0 8px 8px 0;
}
.assistant-float button:focus-visible,
.assistant-float a:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
@media (max-width: 991px) {
  .assistant-float__launcher:not(.is-docked) {
    width: 60px;
    height: 68px;
  }
}
</style>
