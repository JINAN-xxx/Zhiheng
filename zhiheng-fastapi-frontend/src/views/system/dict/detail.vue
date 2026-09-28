<template>
  <ResourceDrawer :open="visible" :title="`${row.dictName || '字典数据'} · ${row.dictType || '-'}`" :width="700" @close="emit('update:visible', false)">
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && !dataList.length" description="暂无字典数据" />
      <template v-else-if="dataList.length">
        <a-row :gutter="12" class="stat-row">
          <a-col :span="disabledCount > 0 ? 8 : 12"><a-statistic title="共计条目" :value="dataList.length" /></a-col>
          <a-col :span="disabledCount > 0 ? 8 : 12"><a-statistic title="正常" :value="normalCount" :value-style="{ color: '#389e0d' }" /></a-col>
          <a-col v-if="disabledCount > 0" :span="8"><a-statistic title="停用" :value="disabledCount" :value-style="{ color: '#cf1322' }" /></a-col>
        </a-row>
        <ResourceTable :columns="columns" :rows="dataList" :pagination="false" row-key="dictCode" :scroll="{ x: 520 }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'dictLabel'">
              <a-tag v-if="record.listClass && record.listClass !== 'default'" :color="tagColor(record.listClass)" :class="record.cssClass">{{ record.dictLabel }}</a-tag>
              <span v-else>{{ record.dictLabel }}</span>
            </template>
            <template v-else-if="column.key === 'status'"><a-tag :color="record.status === '0' ? 'green' : 'red'">{{ record.status === '0' ? '正常' : '停用' }}</a-tag></template>
          </template>
        </ResourceTable>
      </template>
    </a-spin>
  </ResourceDrawer>
</template>

<script setup>
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import { listData } from '@/api/system/dict/data'

const props = defineProps({ visible: { type: Boolean, default: false }, row: { type: Object, default: () => ({}) } })
const emit = defineEmits(['update:visible'])
const loading = ref(false)
const dataList = ref([])
let requestGeneration = 0
const normalCount = computed(() => dataList.value.filter((item) => item.status === '0').length)
const disabledCount = computed(() => dataList.value.filter((item) => item.status !== '0').length)
const columns = [
  { title: '标签', dataIndex: 'dictLabel', key: 'dictLabel' },
  { title: '键值', dataIndex: 'dictValue', key: 'dictValue' },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
]
const tagColor = (type) => ({ primary: 'blue', success: 'green', info: 'default', warning: 'orange', danger: 'red' }[type] || 'default')

watch([() => props.visible, () => props.row?.dictType], ([visible]) => {
  if (visible) loadData()
  else {
    requestGeneration += 1
    loading.value = false
    dataList.value = []
  }
})

async function loadData() {
  const dictType = props.row?.dictType
  if (!dictType) return
  const generation = ++requestGeneration
  loading.value = true
  dataList.value = []
  try {
    const response = await listData({ dictType, pageSize: 100, pageNum: 1 })
    if (generation !== requestGeneration || !props.visible || dictType !== props.row?.dictType) return
    dataList.value = response.rows || []
  } catch {
    if (generation === requestGeneration) dataList.value = []
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}
onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped>
.stat-row { margin-bottom: 16px; }
:deep(.ant-statistic) { padding: 10px 14px; border: 1px solid var(--workspace-border); border-radius: 6px; background: var(--workspace-surface-muted); text-align: center; }
</style>
