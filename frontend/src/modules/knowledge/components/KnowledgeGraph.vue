<!-- Adapted from original knowledge graph, MIT licensed. -->
<template>
  <div class="knowledge-graph">
    <div class="knowledge-graph__toolbar">
      <a-button aria-label="放大图谱" @click="zoom(1.2)"><ZoomInOutlined />放大</a-button
      ><a-button aria-label="缩小图谱" @click="zoom(0.8)"><ZoomOutOutlined />缩小</a-button
      ><a-button aria-label="复位图谱" @click="render"><RedoOutlined />复位</a-button
      ><span>拖拽节点或画布，滚轮缩放；聚焦画布可按 +、− 缩放，按 0 复位</span>
    </div>
    <div
      ref="canvas"
      class="knowledge-graph__canvas"
      role="img"
      tabindex="0"
      :aria-label="`知识图谱，${graph.nodes.length} 个节点，${graph.links.length} 个关系`"
      @keydown="onCanvasKey"
    />
    <details class="knowledge-graph__entities">
      <summary>实体与关系 · {{ graph.nodes.length }} 个实体</summary>
      <div class="knowledge-graph__entity-list">
        <a-button
          v-for="node in graph.nodes"
          :key="node.id"
          size="small"
          :aria-label="'查看实体：' + node.name"
          :aria-pressed="selectedNode?.id === node.id"
          @click="selectedNode = node"
          >{{ node.name }}</a-button
        >
      </div>
    </details>
    <section
      v-if="selectedNode"
      class="knowledge-graph__detail"
      aria-label="实体详情"
      aria-live="polite"
    >
      <div>
        <h3>{{ selectedNode.name }}</h3>
        <a-button size="small" aria-label="关闭实体详情" @click="selectedNode = null"
          >关闭</a-button
        >
      </div>
      <pre>{{ selectedNode.tooltipText }}</pre>
      <h4>关联关系</h4>
      <p
        v-for="link in graph.links.filter(
          (item) => item.source === selectedNode?.id || item.target === selectedNode?.id
        )"
        :key="link.id"
      >
        {{ graph.nodes.find((node) => node.id === link.source)?.name ?? link.source }} →
        {{ graph.nodes.find((node) => node.id === link.target)?.name ?? link.target }} ·
        {{ link.tooltipText }}
      </p>
    </section>
    <pre v-if="hoverText" class="knowledge-graph__tooltip" role="tooltip">{{ hoverText }}</pre>
  </div>
</template>

<script setup lang="ts">
import { Button as AButton } from 'ant-design-vue'
import { RedoOutlined, ZoomInOutlined, ZoomOutOutlined } from '@ant-design/icons-vue'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { object } from '../../../shared/validation.ts'
import { isDark } from '../../../shared/theme.ts'
import type { ChartGraph } from '../graphPresentation.ts'
use([GraphChart, CanvasRenderer])
const props = defineProps<{ graph: ChartGraph }>()
const canvas = ref<HTMLDivElement | null>(null),
  hoverText = ref('')
const selectedNode = ref<ChartGraph['nodes'][number] | null>(null)
let chart: ECharts | undefined, observer: ResizeObserver | undefined
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
  hoverText.value = ''
  selectedNode.value = null
  const color = getComputedStyle(canvas.value).getPropertyValue('color')
  const option: EChartsCoreOption = {
    animation: false,
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        data: props.graph.nodes.map((node) => ({ ...node })),
        links: props.graph.links.map((link) => ({ ...link })),
        categories: props.graph.categories,
        force: { repulsion: 230, edgeLength: [75, 140], friction: 0.6 },
        label: {
          show: true,
          position: 'right',
          color,
          fontSize: 12,
          width: 120,
          overflow: 'truncate'
        },
        edgeLabel: { show: false },
        lineStyle: { color: '#a8afb8', opacity: 0.7, curveness: 0.1 },
        emphasis: { focus: 'adjacency', lineStyle: { width: 3 } }
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
    if (data && typeof data === 'object' && !Array.isArray(data))
      selectedNode.value = props.graph.nodes.find((node) => node.id === object(data).id) ?? null
  })
  chart.on('mouseover', (event: unknown) => {
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
watch(isDark, async () => {
  await nextTick()
  render()
})
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = undefined
})
</script>

<style scoped>
.knowledge-graph {
  position: relative;
  width: 100%;
  min-width: 0;
  color: var(--color-text);
}
.knowledge-graph__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.knowledge-graph__toolbar > span {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.knowledge-graph__entities {
  padding: 14px 0;
  border-top: 1px solid var(--gray-150);
  color: var(--color-text-secondary);
  font-size: 13px;
}
.knowledge-graph__entities summary {
  cursor: pointer;
}
.knowledge-graph__entity-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.knowledge-graph__detail {
  padding: 16px;
  background: var(--bg-container);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.knowledge-graph__detail > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.knowledge-graph__detail h3,
.knowledge-graph__detail h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.knowledge-graph__detail pre,
.knowledge-graph__detail p {
  font: inherit;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.knowledge-graph__canvas:focus-visible {
  outline: 2px solid var(--main-500);
  outline-offset: 2px;
}
.knowledge-graph__canvas {
  height: min(60vh, 620px);
  min-height: 320px;
  width: 100%;
  color: inherit;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background-color: var(--bg-container);
  background-image: radial-gradient(var(--gray-200) 1px, transparent 1px);
  background-size: 20px 20px;
}
.knowledge-graph__tooltip {
  position: absolute;
  top: 12px;
  right: 12px;
  max-width: min(400px, calc(100% - 24px));
  max-height: 240px;
  padding: 12px;
  overflow: hidden;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--bg-container, #fff);
  color: var(--color-text);
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  box-shadow: 0 4px 16px #0002;
  font: inherit;
  pointer-events: none;
}
</style>
