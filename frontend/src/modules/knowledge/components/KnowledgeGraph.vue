<!-- Adapted from original knowledge graph, MIT licensed. -->
<template>
  <div ref="container" class="knowledge-graph" @fullscreenchange="syncFullscreen">
    <div class="knowledge-graph__toolbar">
      <div class="knowledge-graph__legend" aria-label="实体类型">
        <span v-for="(category, index) in graph.categories" :key="category.name">
          <i :style="{ backgroundColor: colors[index % colors.length] }" />{{ category.name }}
        </span>
      </div>
      <div class="knowledge-graph__tools">
        <a-button size="small" title="放大图谱" aria-label="放大图谱" @click="zoom(1.2)"
          ><ZoomInOutlined
        /></a-button>
        <a-button size="small" title="缩小图谱" aria-label="缩小图谱" @click="zoom(0.8)"
          ><ZoomOutOutlined
        /></a-button>
        <a-button size="small" title="复位图谱" aria-label="复位图谱" @click="render"
          ><RedoOutlined
        /></a-button>
        <a-button
          v-if="fullscreenSupported"
          size="small"
          :title="fullscreenLabel"
          :aria-label="fullscreenLabel"
          @click="toggleFullscreen"
          ><FullscreenExitOutlined v-if="isFullscreen" /><FullscreenOutlined v-else
        /></a-button>
      </div>
    </div>
    <div class="knowledge-graph__stage">
      <div
        ref="canvas"
        class="knowledge-graph__canvas"
        role="img"
        tabindex="0"
        :aria-label="`知识图谱，${graph.nodes.length} 个节点，${graph.links.length} 个关系`"
        @keydown="onCanvasKey"
      />
      <div v-if="hoverText" class="knowledge-graph__tooltip" role="tooltip">{{ hoverText }}</div>
      <section
        v-if="selectedNode"
        class="knowledge-graph__detail"
        aria-label="实体详情"
        aria-live="polite"
      >
        <div class="knowledge-graph__detail-heading">
          <div>
            <small :style="{ color: colors[selectedNode.category % colors.length] }">{{
              graph.categories[selectedNode.category]?.name
            }}</small>
            <h3>{{ selectedNode.name }}</h3>
          </div>
          <a-button
            type="text"
            size="small"
            aria-label="关闭实体详情"
            title="关闭实体详情"
            @click="selectNode(null)"
            ><CloseOutlined
          /></a-button>
        </div>
        <p>{{ selectedNode.description }}</p>
        <h4>关联关系 · {{ selectedLinks.length }}</h4>
        <ul v-if="selectedLinks.length">
          <li v-for="link in selectedLinks" :key="link.id">
            <button
              v-if="nodesById.has(link.source === selectedNode.id ? link.target : link.source)"
              type="button"
              @click="selectNode(link.source === selectedNode.id ? link.target : link.source)"
            >
              {{ nodesById.get(link.source === selectedNode.id ? link.target : link.source)?.name }}
            </button>
            <span>{{ link.tooltipText }}</span>
          </li>
        </ul>
      </section>
    </div>
    <details class="knowledge-graph__entities">
      <summary>实体列表 · {{ graph.nodes.length }}</summary>
      <div class="knowledge-graph__entity-list">
        <a-button
          v-for="node in graph.nodes"
          :key="node.id"
          size="small"
          :aria-label="'查看实体：' + node.name"
          :aria-pressed="selectedNode?.id === node.id"
          @click="selectNode(node.id)"
          >{{ node.name }}</a-button
        >
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { Button as AButton, message } from 'ant-design-vue'
import {
  CloseOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  RedoOutlined,
  ZoomInOutlined,
  ZoomOutOutlined
} from '@ant-design/icons-vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { LabelLayout } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import { object } from '../../../shared/validation.ts'
import type { ChartGraph } from '../graphPresentation.ts'

use([GraphChart, LabelLayout, CanvasRenderer])
const colors = ['#087f73', '#b14b22', '#4b762d', '#a63e74', '#3267bf', '#866700', '#714bb7']
const props = defineProps<{ graph: ChartGraph }>()
const container = ref<HTMLDivElement | null>(null)
const canvas = ref<HTMLDivElement | null>(null)
const hoverText = ref('')
const selectedId = ref<string | null>(null)
const fullscreenSupported = document.fullscreenEnabled
const isFullscreen = ref(false)
const fullscreenLabel = computed(() => (isFullscreen.value ? '退出全屏' : '全屏查看'))
const nodesById = computed(() => new Map(props.graph.nodes.map((node) => [node.id, node])))
const selectedNode = computed(() =>
  selectedId.value ? nodesById.value.get(selectedId.value) : undefined
)
const selectedLinks = computed(() =>
  props.graph.links.filter(
    (link) => link.source === selectedId.value || link.target === selectedId.value
  )
)
let chart: ECharts | undefined, observer: ResizeObserver | undefined

function syncFullscreen(): void {
  isFullscreen.value = document.fullscreenElement === container.value
}
async function toggleFullscreen(): Promise<void> {
  try {
    if (isFullscreen.value) await document.exitFullscreen()
    else await container.value?.requestFullscreen()
  } catch {
    void message.warning('无法切换全屏')
  }
}
function selectNode(id: string | null): void {
  if (selectedId.value) {
    chart?.dispatchAction({
      type: 'downplay',
      seriesIndex: 0,
      dataIndex: props.graph.nodes.findIndex((node) => node.id === selectedId.value)
    })
  }
  selectedId.value = id
  hoverText.value = ''
  if (id) {
    chart?.dispatchAction({
      type: 'highlight',
      seriesIndex: 0,
      dataIndex: props.graph.nodes.findIndex((node) => node.id === id)
    })
  }
}
function zoom(factor: number): void {
  chart?.dispatchAction({ type: 'graphRoam', seriesIndex: 0, zoom: factor })
}
function onCanvasKey(event: KeyboardEvent): void {
  if (event.key === '+' || event.key === '=') {
    event.preventDefault()
    zoom(1.2)
  } else if (event.key === '-') {
    event.preventDefault()
    zoom(0.8)
  } else if (event.key === '0') {
    event.preventDefault()
    render()
  }
}
function render(): void {
  if (!chart || !canvas.value) return
  selectedId.value = null
  hoverText.value = ''
  const compact = canvas.value.clientWidth < 550
  const option: EChartsCoreOption = {
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    color: colors,
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        scaleLimit: { min: 0.45, max: 5 },
        data: props.graph.nodes.map((node) => {
          const color = colors[node.category % colors.length]
          return {
            ...node,
            symbolSize: compact ? node.symbolSize * 0.78 : node.symbolSize,
            itemStyle: {
              color,
              borderColor: '#fff',
              borderWidth: 1,
              shadowColor: '#20253a38',
              shadowBlur: 7
            }
          }
        }),
        links: props.graph.links,
        categories: props.graph.categories,
        force: {
          repulsion: compact ? 105 : 255,
          edgeLength: compact ? [48, 84] : [78, 150],
          friction: 0.56
        },
        label: {
          show: true,
          position: 'bottom',
          distance: 6,
          color: '#242738',
          fontSize: 11,
          width: compact ? 72 : 118,
          overflow: 'truncate',
          textBorderColor: '#fff',
          textBorderWidth: 3
        },
        labelLayout: { hideOverlap: true },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 5,
        lineStyle: { color: 'source', width: 1.5, opacity: 0.65, curveness: 0.14 },
        emphasis: {
          focus: 'adjacency',
          label: { show: true },
          lineStyle: { opacity: 0.96, width: 2.6 }
        },
        blur: { itemStyle: { opacity: 0.35 }, lineStyle: { opacity: 0.16 } }
      }
    ]
  }
  chart.setOption(option, true)
}
onMounted(async () => {
  await nextTick()
  if (!canvas.value) return
  chart = init(canvas.value)
  chart.on('click', (event: unknown) => {
    const data = object(event).data
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      const id = object(data).id
      if (typeof id === 'string' && nodesById.value.has(id)) selectNode(id)
    }
  })
  chart.on('mouseover', (event: unknown) => {
    if (selectedId.value) return
    const value = object(event).data
    if (!value || typeof value !== 'object' || Array.isArray(value)) return
    const text = object(value).tooltipText
    hoverText.value = typeof text === 'string' ? text : ''
  })
  chart.on('mouseout', () => {
    hoverText.value = ''
  })
  chart.on('globalout', () => {
    hoverText.value = ''
  })
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(canvas.value)
  render()
})
watch(() => props.graph, render)
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = undefined
})
</script>

<style scoped>
.knowledge-graph {
  width: 100%;
  min-width: 0;
}
.knowledge-graph:fullscreen {
  display: flex;
  flex-direction: column;
  background: #fff;
}
.knowledge-graph:fullscreen .knowledge-graph__stage {
  flex: 1;
  min-height: 0;
}
.knowledge-graph:fullscreen .knowledge-graph__canvas {
  height: 100%;
  min-height: 0;
}
.knowledge-graph:fullscreen .knowledge-graph__entities {
  max-height: 25vh;
  overflow: auto;
  padding: 8px 12px;
}
.knowledge-graph__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  color: var(--color-text-secondary);
  background: #fff;
  border: 1px solid var(--gray-150);
  border-radius: 8px 8px 0 0;
}
.knowledge-graph__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  min-width: 0;
  max-height: 92px;
  overflow: auto;
  font-size: 12px;
}
.knowledge-graph__legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  overflow-wrap: anywhere;
}
.knowledge-graph__legend i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 8px;
}
.knowledge-graph__tools {
  display: flex;
  gap: 6px;
  margin-left: auto;
}
.knowledge-graph__tools :deep(.ant-btn) {
  width: 30px;
  height: 30px;
  padding: 0;
  color: var(--color-text);
  background: #fff;
  border-color: var(--gray-150);
}
.knowledge-graph__tools :deep(.ant-btn:hover) {
  color: var(--main-color);
  border-color: var(--main-color);
}
.knowledge-graph__stage {
  position: relative;
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-top: 0;
  border-radius: 0 0 8px 8px;
  background-color: #fff;
  background-image:
    linear-gradient(#edf0f5 1px, transparent 1px),
    linear-gradient(90deg, #edf0f5 1px, transparent 1px);
  background-size: 24px 24px;
}
.knowledge-graph__canvas {
  width: 100%;
  height: min(62vh, 640px);
  min-height: 340px;
}
.knowledge-graph__canvas:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}
.knowledge-graph__detail,
.knowledge-graph__tooltip {
  position: absolute;
  z-index: 1;
  top: 12px;
  right: 12px;
  width: min(300px, calc(100% - 24px));
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: #fff;
  color: var(--color-text);
  box-shadow: 0 4px 18px #20253a1a;
  overflow: auto;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.55;
}
.knowledge-graph__tooltip {
  max-height: 140px;
  white-space: pre-wrap;
  pointer-events: none;
}
.knowledge-graph__detail {
  max-height: calc(100% - 24px);
}
.knowledge-graph__detail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.knowledge-graph__detail-heading :deep(.ant-btn) {
  color: var(--color-text-secondary);
  flex: 0 0 auto;
}
.knowledge-graph__detail h3,
.knowledge-graph__detail h4 {
  color: var(--color-text);
  margin: 0;
  font-size: 14px;
}
.knowledge-graph__detail h4 {
  margin-top: 12px;
  font-size: 12px;
}
.knowledge-graph__detail ul {
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}
.knowledge-graph__detail li {
  display: grid;
  gap: 3px;
  padding: 7px 0;
  border-bottom: 1px solid var(--gray-150);
}
.knowledge-graph__detail li button {
  padding: 0;
  background: none;
  border: 0;
  color: var(--main-color);
  text-align: start;
  cursor: pointer;
}
.knowledge-graph__entities {
  padding: 10px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.knowledge-graph__entities summary {
  cursor: pointer;
}
.knowledge-graph__entity-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.knowledge-graph__entity-list :deep(.ant-btn) {
  max-width: 100%;
  height: auto;
  white-space: normal;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .knowledge-graph__detail {
    top: auto;
    bottom: 10px;
    left: 10px;
    right: 10px;
    width: auto;
    max-height: 45%;
  }
}
</style>
