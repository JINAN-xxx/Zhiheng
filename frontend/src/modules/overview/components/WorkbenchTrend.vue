<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import type { EChartsType } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { DailyCalls } from '../api/index.ts'
use([LineChart, GridComponent, TooltipComponent, SVGRenderer])
const props = defineProps<{ points: DailyCalls[] }>()
const target = ref<HTMLElement | null>(null)
let chart: EChartsType | null = null
let observer: ResizeObserver | null = null
function render() {
  chart?.setOption(
    {
      animation: false,
      color: ['#6c5cff'],
      grid: { left: 42, right: 18, top: 20, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        confine: true,
        valueFormatter: (value: number) => `${value.toLocaleString()} 次`
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: props.points.map((p) => p.day.slice(5).replace('-', '/')),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#9299ad', fontSize: 11, margin: 14 }
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitNumber: 3,
        axisLabel: { color: '#9299ad', fontSize: 11 },
        splitLine: { lineStyle: { color: '#f0f1f7' } }
      },
      series: [
        {
          type: 'line',
          data: props.points.map((p) => p.calls),
          smooth: 0.25,
          symbol: 'circle',
          symbolSize: 7,
          lineStyle: { width: 2.5, color: '#6c5cff' },
          itemStyle: { borderWidth: 2, borderColor: '#6c5cff', color: '#fff' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(108,92,255,0.19)' },
                { offset: 1, color: 'rgba(108,92,255,0.015)' }
              ]
            }
          }
        }
      ]
    },
    true
  )
}
onMounted(() => {
  if (!target.value) return
  chart = init(target.value, undefined, { renderer: 'svg' })
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(target.value)
})
watch(() => props.points, render, { deep: true })
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <div
    ref="target"
    class="workbench-chart"
    role="img"
    :aria-label="'每日模型调用次数：' + points.map((p) => p.day + ' ' + p.calls + '次').join('；')"
  />
</template>
<style scoped>
.workbench-chart {
  width: 100%;
  height: 236px;
  min-width: 0;
}
@media (max-width: 600px) {
  .workbench-chart {
    height: 210px;
  }
}
</style>
