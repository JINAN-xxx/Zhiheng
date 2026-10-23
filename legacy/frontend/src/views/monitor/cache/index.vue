<template>
  <WorkspacePage title="缓存监控" subtitle="Redis 运行状态与资源统计"><template #actions><a-button :loading="loading" @click="getList"><ReloadOutlined />刷新</a-button></template>
    <a-spin :spinning="loading"><a-card title="基本信息" class="monitor-card"><a-descriptions bordered :column="4" size="small">
      <a-descriptions-item label="Redis版本">{{ info.redis_version || '-' }}</a-descriptions-item><a-descriptions-item label="运行模式">{{ info.redis_mode === 'standalone' ? '单机' : '集群' }}</a-descriptions-item><a-descriptions-item label="端口">{{ info.tcp_port || '-' }}</a-descriptions-item><a-descriptions-item label="客户端数">{{ info.connected_clients || '-' }}</a-descriptions-item>
      <a-descriptions-item label="运行时间(天)">{{ info.uptime_in_days || '-' }}</a-descriptions-item><a-descriptions-item label="使用内存">{{ info.used_memory_human || '-' }}</a-descriptions-item><a-descriptions-item label="使用CPU">{{ cpuUsage }}</a-descriptions-item><a-descriptions-item label="内存配置">{{ info.maxmemory_human || '-' }}</a-descriptions-item>
      <a-descriptions-item label="AOF是否开启">{{ info.aof_enabled === '0' ? '否' : '是' }}</a-descriptions-item><a-descriptions-item label="RDB是否成功">{{ info.rdb_last_bgsave_status || '-' }}</a-descriptions-item><a-descriptions-item label="Key数量">{{ cache.dbSize ?? '-' }}</a-descriptions-item><a-descriptions-item label="网络入口/出口">{{ info.instantaneous_input_kbps || 0 }}kps / {{ info.instantaneous_output_kbps || 0 }}kps</a-descriptions-item>
    </a-descriptions></a-card><a-row :gutter="16"><a-col :xs="24" :lg="12"><a-card title="命令统计"><div ref="commandstats" class="chart" /></a-card></a-col><a-col :xs="24" :lg="12"><a-card title="内存信息"><div ref="usedmemory" class="chart" /></a-card></a-col></a-row></a-spin>
  </WorkspacePage>
</template>
<script setup name="Cache">
import { ReloadOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import { getCache } from '@/api/monitor/cache'
import * as echarts from 'echarts'
const cache=ref({}), commandstats=ref(), usedmemory=ref(), loading=ref(false)
const info=computed(()=>cache.value.info||{})
const cpuUsage=computed(()=>Number(info.value.used_cpu_user_children||0).toFixed(2))
let commandChart, memoryChart
async function getList(){loading.value=true;try{const response=await getCache();cache.value=response.data||{};await nextTick();commandChart?.dispose();memoryChart?.dispose();commandChart=echarts.init(commandstats.value);commandChart.setOption({tooltip:{trigger:'item'},series:[{name:'命令',type:'pie',roseType:'radius',radius:[15,95],center:['50%','45%'],data:response.data.commandStats||[]}]});memoryChart=echarts.init(usedmemory.value);memoryChart.setOption({series:[{name:'峰值',type:'gauge',min:0,max:1000,detail:{formatter:info.value.used_memory_human||'-'},data:[{value:parseFloat(info.value.used_memory_human)||0,name:'内存消耗'}]}]})}finally{loading.value=false}}
function resize(){commandChart?.resize();memoryChart?.resize()}
onMounted(()=>{getList();window.addEventListener('resize',resize)})
onBeforeUnmount(()=>{window.removeEventListener('resize',resize);commandChart?.dispose();memoryChart?.dispose()})
</script>
<style scoped>.monitor-card{margin-bottom:16px}.chart{height:420px}@media(max-width:767px){:deep(.ant-descriptions-view){overflow-x:auto}}</style>
