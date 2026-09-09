<template>
  <div class="knowledge-graph">
    <div ref="canvas" class="knowledge-graph__canvas" role="img" :aria-label="`知识图谱，${graph.nodes.length} 个节点，${graph.links.length} 个关系`" />
    <pre v-if="hoverText" class="knowledge-graph__tooltip" role="tooltip">{{ hoverText }}</pre>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'

use([GraphChart, CanvasRenderer])
const props = defineProps({ graph: { type: Object, required: true } })
const canvas = ref()
const hoverText = ref('')
let chart, observer

function render() {
  if (!chart) return
  hoverText.value = ''
  const color = getComputedStyle(canvas.value).getPropertyValue('color')
  chart.setOption({
    animation: false,
    series: [{ type: 'graph', layout: 'force', roam: true, draggable: true,
      data: props.graph.nodes.map(node => ({ ...node })), links: props.graph.links.map(link => ({ ...link })),
      categories: props.graph.categories, force: { repulsion: 230, edgeLength: [75, 140], friction: 0.6 },
      label: { show: true, position: 'right', color, fontSize: 12, width: 120, overflow: 'truncate' },
      edgeLabel: { show: false }, lineStyle: { color: '#a8afb8', opacity: 0.7, curveness: 0.1 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
    }],
  }, true)
}

onMounted(async () => {
  await nextTick()
  chart = init(canvas.value)
  // Display untrusted graph values through Vue text interpolation, never an HTML formatter.
  chart.on('mouseover', event => { hoverText.value = event.data?.tooltipText || '' })
  chart.on('mouseout', () => { hoverText.value = '' })
  chart.on('globalout', () => { hoverText.value = '' })
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(canvas.value)
  render()
})
watch(() => props.graph, render)
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose(); chart = null })
</script>

<style scoped>
.knowledge-graph { position: relative; width: 100%; min-width: 0; color: var(--color-text); }
.knowledge-graph__canvas { height: min(60vh, 620px); min-height: 320px; width: 100%; color: inherit; }
.knowledge-graph__tooltip { position: absolute; top: 12px; right: 12px; max-width: min(400px, calc(100% - 24px)); max-height: 240px; padding: 12px; overflow: hidden; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg-container, #fff); color: var(--color-text); border: 1px solid var(--gray-150); border-radius: 6px; box-shadow: 0 4px 16px #0002; font: inherit; pointer-events: none; }
</style>
