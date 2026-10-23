<template>
  <WorkspacePage title="传输加密监控" subtitle="观察加密请求、密钥版本与失败原因">
    <a-row :gutter="16" class="mb16">
      <a-col :xs="24" :sm="12" :lg="6">
        <a-card class="summary-card">
          <div class="summary-header">
            <span class="summary-title">传输加密状态</span>
            <a-tag :color="monitorData.transportCryptoEnabled ? 'green' : 'default'">
              {{ monitorData.transportCryptoEnabled ? '已启用' : '未启用' }}
            </a-tag>
          </div>
          <div class="summary-value">{{ modeLabel }}</div>
          <div class="summary-desc">
            当前模式：{{ monitorData.transportCryptoMode || '-' }}
          </div>
        </a-card>
      </a-col>

      <a-col :xs="24" :sm="12" :lg="6">
        <a-card class="summary-card">
          <div class="summary-header">
            <span class="summary-title">请求总览</span>
            <a-tag color="blue">命中规则</a-tag>
          </div>
          <div class="summary-value">{{ formatCount(monitorData.requestsTotal) }}</div>
          <div class="summary-desc">
            明文 {{ formatCount(monitorData.plainRequestsTotal) }} / 加密 {{ formatCount(monitorData.encryptedRequestsTotal) }}
          </div>
        </a-card>
      </a-col>

      <a-col :xs="24" :sm="12" :lg="6">
        <a-card class="summary-card">
          <div class="summary-header">
            <span class="summary-title">解密成功率</span>
            <a-tag :color="decryptSuccessRate >= 95 ? 'green' : decryptSuccessRate >= 80 ? 'orange' : 'red'">
              {{ decryptSuccessRate.toFixed(1) }}%
            </a-tag>
          </div>
          <div class="summary-value">{{ formatCount(monitorData.decryptSuccessTotal) }}</div>
          <div class="summary-desc">
            失败 {{ formatCount(monitorData.decryptFailureTotal) }} / 强制拒绝 {{ formatCount(monitorData.requiredRejectedTotal) }}
          </div>
        </a-card>
      </a-col>

      <a-col :xs="24" :sm="12" :lg="6">
        <a-card class="summary-card">
          <div class="summary-header">
            <span class="summary-title">响应加密</span>
            <a-tag color="orange">JSON 响应</a-tag>
          </div>
          <div class="summary-value">{{ formatCount(monitorData.encryptedResponsesTotal) }}</div>
          <div class="summary-desc">
            明文 {{ formatCount(monitorData.plainResponsesTotal) }} / 错误加密 {{ formatCount(monitorData.encryptedErrorResponsesTotal) }}
          </div>
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" class="mb16">
      <a-col :xs="24" :lg="16">
        <a-card>
          <template #title>
            <div class="card-header">
              <span>当前配置</span>
              <div class="card-actions">
                <a-switch
                  v-model:checked="autoRefresh"
                  checked-children="自动刷新"
                  un-checked-children="手动"
                />
                <a-button type="primary" :loading="loading" @click="loadMonitorData"><ReloadOutlined />刷新</a-button>
              </div>
            </div>
          </template>

          <a-descriptions :column="2" bordered>
            <a-descriptions-item label="统计范围">
              {{ monitorScopeLabel }}
            </a-descriptions-item>
            <a-descriptions-item label="应用环境">
              {{ monitorData.appEnv || '-' }}
            </a-descriptions-item>
            <a-descriptions-item label="当前密钥版本">
              <a-tag>{{ monitorData.currentKid || '-' }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="监控起始时间">
              {{ formatMonitorTime(monitorData.startedAt) || '-' }}
            </a-descriptions-item>
            <a-descriptions-item label="支持的密钥版本">
              <div class="tag-list">
                <a-tag v-for="kid in monitorData.supportedKids || []" :key="kid" class="tag-item">
                  {{ kid }}
                </a-tag>
                <span v-if="!(monitorData.supportedKids || []).length">-</span>
              </div>
            </a-descriptions-item>
            <a-descriptions-item label="失败原因种类">
              {{ failureReasonRows.length }}
            </a-descriptions-item>
            <a-descriptions-item label="聚合说明" :span="2">
              {{ monitorScopeDescription }}
            </a-descriptions-item>
            <a-descriptions-item label="启用路径">
              <div class="tag-list">
                <a-tag
                  v-for="path in monitorData.enabledPaths || []"
                  :key="path"
                  color="green"
                  class="tag-item"
                  effect="plain"
                >
                  {{ path }}
                </a-tag>
                <span v-if="!(monitorData.enabledPaths || []).length">全部接口</span>
              </div>
            </a-descriptions-item>
            <a-descriptions-item label="强制加密路径">
              <div class="tag-list">
                <a-tag
                  v-for="path in monitorData.requiredPaths || []"
                  :key="path"
                  color="orange"
                  class="tag-item"
                  effect="plain"
                >
                  {{ path }}
                </a-tag>
                <span v-if="!(monitorData.requiredPaths || []).length">-</span>
              </div>
            </a-descriptions-item>
            <a-descriptions-item label="排除路径" :span="2">
              <div class="tag-list">
                <a-tag
                  v-for="path in monitorData.excludePaths || []"
                  :key="path"
                  color="default"
                  class="tag-item"
                  effect="plain"
                >
                  {{ path }}
                </a-tag>
                <span v-if="!(monitorData.excludePaths || []).length">-</span>
              </div>
            </a-descriptions-item>
          </a-descriptions>
        </a-card>
      </a-col>

      <a-col :xs="24" :lg="8">
        <a-card class="health-card">
          <template #title>
            <div class="card-header">
              <span>运行健康度</span>
              <a-tag :color="tagColor(healthTagLabelType)">{{ healthLabel }}</a-tag>
            </div>
          </template>

          <div class="health-item">
            <div class="health-label">
              <span>解密成功率</span>
              <span>{{ decryptSuccessRate.toFixed(1) }}%</span>
            </div>
            <a-progress :percentage="Number(decryptSuccessRate.toFixed(1))" :status="healthProgressStatus" />
          </div>

          <div class="health-item">
            <div class="health-label">
              <span>加密请求占比</span>
              <span>{{ encryptedRequestRate.toFixed(1) }}%</span>
            </div>
            <a-progress :percentage="Number(encryptedRequestRate.toFixed(1))" status="success" />
          </div>

          <div class="health-item">
            <div class="health-label">
              <span>加密响应占比</span>
              <span>{{ encryptedResponseRate.toFixed(1) }}%</span>
            </div>
            <a-progress :percentage="Number(encryptedResponseRate.toFixed(1))" />
          </div>

          <a-alert
            :message="healthMessage"
            :type="healthTagType"
            :closable="false"
            show-icon
          />
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" class="mb16">
      <a-col :xs="24" :lg="10">
        <a-card>
          <template #title>
            <div class="card-header">
              <span>失败原因统计</span>
              <div class="card-actions compact-actions">
                <span class="card-subtitle">按 Redis 聚合口径统计</span>
                <a-tag v-if="selectedFailureReason" color="red" closable @close="clearFailureReasonSelection">
                  {{ selectedFailureReason }}
                </a-tag>
              </div>
            </div>
          </template>

          <div v-if="failureReasonRows.length" ref="failureReasonChartRef" class="chart-panel" />
          <a-empty v-else description="暂无失败记录" :image-style="{ height: '88px' }" class="chart-empty" />

          <div class="table-title">明细数据</div>
          <a-table :columns="failureColumns" :data-source="displayedFailureReasonRows" :pagination="false" :scroll="{ y: 260 }" row-key="reason" :row-class-name="failureRowClass" :custom-row="failureCustomRow">
            <template #bodyCell="{ column, record }"><template v-if="column.key === 'reason'"><a-tag :color="tagColor(getFailureTagType(record.reason))">{{ record.reason }}</a-tag></template><template v-else-if="column.key === 'percent'">{{ formatPercent(record.count, totalFailureReasonCount) }}</template></template>
          </a-table>
        </a-card>
      </a-col>

      <a-col :xs="24" :lg="14">
        <a-card>
          <template #title>
            <div class="card-header">
              <span>密钥版本统计</span>
              <div class="card-actions compact-actions">
                <span class="card-subtitle">观察不同 kid 的运行状态</span>
                <a-tag v-if="selectedKid" color="green" closable @close="clearKidSelection">
                  {{ selectedKid }}
                </a-tag>
              </div>
            </div>
          </template>

          <div v-if="kidStatRows.length" ref="kidStatsChartRef" class="chart-panel" />
          <a-empty v-else description="暂无密钥统计数据" :image-style="{ height: '88px' }" class="chart-empty" />

          <div class="table-title">明细数据</div>
          <a-table :columns="kidColumns" :data-source="displayedKidStatRows" :pagination="false" :scroll="{ x: 720, y: 260 }" row-key="kid" :row-class-name="kidRowClass" :custom-row="kidCustomRow">
            <template #bodyCell="{ column, record }"><template v-if="column.key === 'kid'"><a-tag :color="record.kid === monitorData.currentKid ? 'green' : 'default'">{{ record.kid || '-' }}</a-tag></template><template v-else-if="column.key === 'rate'">{{ formatRate(record.decryptSuccess, record.decryptSuccess + record.decryptFailure) }}</template></template>
          </a-table>
        </a-card>
      </a-col>
    </a-row>

    <a-card>
      <template #title>
        <div class="card-header">
          <span>最近失败记录</span>
          <div class="card-actions compact-actions">
            <span class="card-subtitle">最近 {{ displayedRecentFailures.length }} 条</span>
            <a-tag
              v-if="selectedFailureReason || selectedKid"
              type="info"
              effect="plain"
            >
              已按当前选择联动筛选
            </a-tag>
          </div>
        </div>
      </template>

      <a-table :columns="recentColumns" :data-source="displayedRecentFailures" :pagination="false" :scroll="{ x: 850 }" :row-key="recentRowKey">
        <template #bodyCell="{ column, record }"><template v-if="column.key === 'time'">{{ formatMonitorTime(record.time) || '-' }}</template><template v-else-if="column.key === 'method'"><a-tag>{{ record.method || '-' }}</a-tag></template><template v-else-if="column.key === 'reason'"><a-tag :color="tagColor(getFailureTagType(record.reason))">{{ record.reason || '-' }}</a-tag></template><template v-else-if="column.key === 'kid'">{{ record.kid || '-' }}</template></template>
      </a-table>
    </a-card>
  </WorkspacePage>
</template>

<script setup name="TransportCryptoMonitor">
import { getTransportCryptoMonitor } from '@/api/monitor/transportCrypto'
import { parseTime } from '@/utils/zhiheng'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import useSettingsStore from '@/store/modules/settings'
import * as echarts from 'echarts'

const loading = ref(false)
const autoRefresh = ref(true)
const settingsStore = useSettingsStore()
const failureReasonChartRef = ref(null)
const kidStatsChartRef = ref(null)
const selectedFailureReason = ref('')
const selectedKid = ref('')
const monitorData = ref({
  supportedKids: [],
  enabledPaths: [],
  requiredPaths: [],
  excludePaths: [],
  kidStats: [],
  recentFailures: [],
  failureReasons: {}
})

let refreshTimer = null
let monitorGeneration = 0
let globalLoadingOpen = false
let failureReasonChartInstance = null
let kidStatsChartInstance = null

const failureColumns = [
  { title: '失败原因', dataIndex: 'reason', key: 'reason' },
  { title: '次数', dataIndex: 'count', key: 'count', width: 100, align: 'center' },
  { title: '占比', key: 'percent', width: 120, align: 'center' },
]
const kidColumns = [
  { title: '密钥版本', dataIndex: 'kid', key: 'kid', width: 140 },
  { title: '加密请求', dataIndex: 'encryptedRequests', key: 'encryptedRequests', width: 110, align: 'center' },
  { title: '解密成功', dataIndex: 'decryptSuccess', key: 'decryptSuccess', width: 110, align: 'center' },
  { title: '解密失败', dataIndex: 'decryptFailure', key: 'decryptFailure', width: 110, align: 'center' },
  { title: '加密响应', dataIndex: 'encryptedResponses', key: 'encryptedResponses', width: 110, align: 'center' },
  { title: '成功率', key: 'rate', width: 120, align: 'center' },
]
const recentColumns = [
  { title: '时间', key: 'time', width: 170 }, { title: '请求方法', key: 'method', width: 100, align: 'center' },
  { title: '请求路径', dataIndex: 'path', key: 'path', width: 260, ellipsis: true }, { title: '失败原因', key: 'reason', width: 180 },
  { title: '密钥版本', key: 'kid', width: 140 },
]
const tagColor = (type) => ({ success: 'green', warning: 'orange', danger: 'red', primary: 'blue', info: 'default' }[type] || 'default')
const failureRowClass = (row) => selectedFailureReason.value && row.reason === selectedFailureReason.value ? 'selected-table-row' : ''
const kidRowClass = (row) => selectedKid.value && row.kid === selectedKid.value ? 'selected-table-row' : ''
const failureCustomRow = (row) => ({ onClick: () => handleFailureReasonRowClick(row) })
const kidCustomRow = (row) => ({ onClick: () => handleKidStatRowClick(row) })
const recentRowKey = (row) => `${row.time || 'failure'}-${row.method || ''}-${row.path || ''}-${row.reason || ''}-${row.kid || ''}`

const modeLabelMap = {
  required: '强制加密',
  optional: '可选加密',
  off: '已关闭'
}

const monitorScopeLabelMap = {
  'redis-aggregated': 'Redis 聚合',
  'redis-aggregated+local-fallback': 'Redis 聚合 + 本地回退',
  'process-local-fallback': '本地回退'
}

const modeLabel = computed(() => modeLabelMap[monitorData.value.transportCryptoMode] || '未配置')

const monitorScopeLabel = computed(() => monitorScopeLabelMap[monitorData.value.monitorScope] || monitorData.value.monitorScope || '-')

const monitorScopeDescription = computed(() => {
  if (monitorData.value.monitorScope === 'redis-aggregated') {
    return '统计结果已聚合到 Redis，可覆盖多 worker / 多实例共享的监控口径。'
  }
  if (monitorData.value.monitorScope === 'redis-aggregated+local-fallback') {
    return '当前以 Redis 聚合为主，部分监控写入曾降级到本地内存，建议检查 Redis 连接稳定性。'
  }
  if (monitorData.value.monitorScope === 'process-local-fallback') {
    return '当前监控未写入 Redis，页面展示的是本进程本地统计，请优先检查 Redis 可用性。'
  }
  return '当前展示传输加密运行状态与统计信息。'
})

const failureReasonRows = computed(() => {
  const failureReasons = monitorData.value.failureReasons || {}
  return Object.keys(failureReasons)
    .map(key => ({
      reason: key,
      count: failureReasons[key]
    }))
    .sort((a, b) => b.count - a.count)
})

const totalFailureReasonCount = computed(() =>
  failureReasonRows.value.reduce((total, item) => total + Number(item.count || 0), 0)
)

const displayedFailureReasonRows = computed(() => {
  if (!selectedFailureReason.value) {
    return failureReasonRows.value
  }
  return failureReasonRows.value.filter(item => item.reason === selectedFailureReason.value)
})

const kidStatRows = computed(() =>
  [...(monitorData.value.kidStats || [])].sort((a, b) => {
    const currentKid = monitorData.value.currentKid
    if (a.kid === currentKid && b.kid !== currentKid) {
      return -1
    }
    if (b.kid === currentKid && a.kid !== currentKid) {
      return 1
    }
    const aTotal = Number(a.encryptedRequests || 0) + Number(a.decryptSuccess || 0) + Number(a.decryptFailure || 0)
    const bTotal = Number(b.encryptedRequests || 0) + Number(b.decryptSuccess || 0) + Number(b.decryptFailure || 0)
    return bTotal - aTotal
  })
)

const displayedKidStatRows = computed(() => {
  if (!selectedKid.value) {
    return kidStatRows.value
  }
  return kidStatRows.value.filter(item => item.kid === selectedKid.value)
})

const displayedRecentFailures = computed(() => {
  return (monitorData.value.recentFailures || []).filter(item => {
    const matchesReason = !selectedFailureReason.value || item.reason === selectedFailureReason.value
    const matchesKid = !selectedKid.value || item.kid === selectedKid.value
    return matchesReason && matchesKid
  })
})

const decryptSuccessRate = computed(() =>
  getRate(monitorData.value.decryptSuccessTotal, monitorData.value.decryptSuccessTotal + monitorData.value.decryptFailureTotal)
)

const encryptedRequestRate = computed(() =>
  getRate(monitorData.value.encryptedRequestsTotal, monitorData.value.requestsTotal)
)

const encryptedResponseRate = computed(() =>
  getRate(monitorData.value.encryptedResponsesTotal, monitorData.value.encryptedResponsesTotal + monitorData.value.plainResponsesTotal)
)

const healthLabel = computed(() => {
  if ((monitorData.value.decryptFailureTotal || 0) === 0) {
    return '稳定'
  }
  if (decryptSuccessRate.value >= 95) {
    return '良好'
  }
  if (decryptSuccessRate.value >= 80) {
    return '关注'
  }
  return '告警'
})

const healthTagType = computed(() => {
  if ((monitorData.value.decryptFailureTotal || 0) === 0) {
    return 'success'
  }
  if (decryptSuccessRate.value >= 95) {
    return 'success'
  }
  if (decryptSuccessRate.value >= 80) {
    return 'warning'
  }
  return 'error'
})

const healthTagLabelType = computed(() => healthTagType.value === 'error' ? 'danger' : healthTagType.value)

const healthProgressStatus = computed(() => {
  if (decryptSuccessRate.value >= 95) {
    return 'success'
  }
  if (decryptSuccessRate.value >= 80) {
    return 'normal'
  }
  return 'exception'
})

const healthMessage = computed(() => {
  if ((monitorData.value.decryptFailureTotal || 0) === 0) {
    return '当前暂无解密失败记录，链路运行稳定。'
  }
  if (decryptSuccessRate.value >= 95) {
    return '存在少量失败事件，建议继续观察失败原因分布。'
  }
  if (decryptSuccessRate.value >= 80) {
    return '近期已有一定比例的异常请求，建议优先检查失败原因与最近失败记录。'
  }
  return '异常比例偏高，建议立即排查密钥版本、AAD 绑定、时间窗与重放校验。'
})

function formatMonitorTime(value, pattern = '{y}-{m}-{d} {h}:{i}:{s}') {
  if (!value) {
    return null
  }
  if (typeof value === 'string') {
    const normalizedValue = value.trim()
    const microsecondIsoMatch = normalizedValue.match(
      /^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?$/
    )
    if (microsecondIsoMatch) {
      const [, year, month, day, hour, minute, second] = microsecondIsoMatch
      return parseTime(
        new Date(
          Number(year),
          Number(month) - 1,
          Number(day),
          Number(hour),
          Number(minute),
          Number(second)
        ),
        pattern
      )
    }
  }
  return parseTime(value, pattern)
}

function loadMonitorData(showLoading = true) {
  const generation = ++monitorGeneration
  loading.value = true
  if (showLoading) {
    feedback.loading('正在加载传输加密监控数据，请稍候！')
    globalLoadingOpen = true
  }
  getTransportCryptoMonitor().then(response => {
    if (generation !== monitorGeneration) return
    monitorData.value = {
      supportedKids: [],
      enabledPaths: [],
      requiredPaths: [],
      excludePaths: [],
      kidStats: [],
      recentFailures: [],
      failureReasons: {},
      ...response.data
    }
    nextTick(() => {
      renderFailureReasonChart()
      renderKidStatsChart()
    })
  }).finally(() => {
    if (generation !== monitorGeneration) return
    loading.value = false
    if (globalLoadingOpen) {
      feedback.closeLoading()
      globalLoadingOpen = false
    }
  })
}

function resetRefreshTimer() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
  if (autoRefresh.value) {
    refreshTimer = setInterval(() => {
      loadMonitorData(false)
    }, 15000)
  }
}

function formatCount(value) {
  return Number(value || 0)
}

function formatPercent(value, total) {
  if (!total) {
    return '0.0%'
  }
  return `${((Number(value || 0) / total) * 100).toFixed(1)}%`
}

function formatRate(success, total) {
  return `${getRate(success, total).toFixed(1)}%`
}

function getRate(numerator, denominator) {
  if (!denominator) {
    return 0
  }
  return (Number(numerator || 0) / Number(denominator || 0)) * 100
}

function getFailureTagType(reason) {
  const warningReasons = ['timestamp_expired', 'required_missing']
  const dangerReasons = ['decrypt_failed', 'aad_mismatch', 'replay_detected', 'kid_mismatch']

  if (dangerReasons.includes(reason)) {
    return 'danger'
  }
  if (warningReasons.includes(reason)) {
    return 'warning'
  }
  return 'info'
}

function resolveThemeColor(token, fallback) {
  if (typeof document === 'undefined' || !document.documentElement || typeof getComputedStyle !== 'function') {
    return fallback
  }
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback
}

function getFailureChartColor(reason) {
  const tagType = getFailureTagType(reason)
  if (tagType === 'danger') {
    return resolveThemeColor('--color-error-500', '#ff4d4f')
  }
  if (tagType === 'warning') {
    return resolveThemeColor('--color-warning-500', '#faad14')
  }
  return resolveThemeColor('--color-info-500', '#1890ff')
}

function buildKidChartBarData(item, value, color) {
  const isSelected = !selectedKid.value || selectedKid.value === item.kid
  return {
    value: Number(value || 0),
    itemStyle: {
      color,
      opacity: isSelected ? 1 : 0.3
    }
  }
}

function bindFailureReasonChartEvents(chartInstance) {
  chartInstance.off('click')
  chartInstance.on('click', params => {
    const targetReason = failureReasonRows.value[params?.dataIndex]?.reason
    if (!targetReason) {
      return
    }
    selectedFailureReason.value = selectedFailureReason.value === targetReason ? '' : targetReason
  })
}

function bindKidStatsChartEvents(chartInstance) {
  chartInstance.off('click')
  chartInstance.on('click', params => {
    const targetKid = kidStatRows.value[params?.dataIndex]?.kid
    if (!targetKid) {
      return
    }
    selectedKid.value = selectedKid.value === targetKid ? '' : targetKid
  })
}

function initFailureReasonChart() {
  if (!failureReasonChartRef.value) {
    if (failureReasonChartInstance) {
      failureReasonChartInstance.dispose()
      failureReasonChartInstance = null
    }
    return null
  }
  if (!failureReasonChartInstance) {
    failureReasonChartInstance = echarts.init(failureReasonChartRef.value)
    bindFailureReasonChartEvents(failureReasonChartInstance)
  }
  return failureReasonChartInstance
}

function initKidStatsChart() {
  if (!kidStatsChartRef.value) {
    if (kidStatsChartInstance) {
      kidStatsChartInstance.dispose()
      kidStatsChartInstance = null
    }
    return null
  }
  if (!kidStatsChartInstance) {
    kidStatsChartInstance = echarts.init(kidStatsChartRef.value)
    bindKidStatsChartEvents(kidStatsChartInstance)
  }
  return kidStatsChartInstance
}

function renderFailureReasonChart() {
  if (!failureReasonRows.value.length) {
    if (failureReasonChartInstance) {
      failureReasonChartInstance.dispose()
      failureReasonChartInstance = null
    }
    return
  }
  const chartInstance = initFailureReasonChart()
  if (!chartInstance) {
    return
  }

  chartInstance.setOption({
    animationDuration: 400,
    color: failureReasonRows.value.map(item => getFailureChartColor(item.reason)),
    grid: {
      top: 16,
      left: 120,
      right: 24,
      bottom: 16,
      containLabel: true
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      },
      formatter(params) {
        const currentItem = params?.[0]
        if (!currentItem) {
          return ''
        }
        const currentRow = failureReasonRows.value[currentItem.dataIndex]
        return `${currentRow.reason}<br/>次数：${currentRow.count}<br/>占比：${formatPercent(currentRow.count, totalFailureReasonCount.value)}`
      }
    },
    xAxis: {
      type: 'value',
      splitLine: {
        lineStyle: {
          type: 'dashed'
        }
      }
    },
    yAxis: {
      type: 'category',
      data: failureReasonRows.value.map(item => item.reason),
      axisTick: {
        show: false
      }
    },
    series: [
      {
        name: '失败次数',
        type: 'bar',
        barMaxWidth: 22,
        data: failureReasonRows.value.map(item => ({
          value: Number(item.count || 0),
          itemStyle: {
            color: getFailureChartColor(item.reason),
            opacity: !selectedFailureReason.value || selectedFailureReason.value === item.reason ? 1 : 0.35,
            borderRadius: [0, 6, 6, 0]
          }
        })),
        label: {
          show: true,
          position: 'right'
        }
      }
    ]
  })
}

function renderKidStatsChart() {
  if (!kidStatRows.value.length) {
    if (kidStatsChartInstance) {
      kidStatsChartInstance.dispose()
      kidStatsChartInstance = null
    }
    return
  }
  const chartInstance = initKidStatsChart()
  if (!chartInstance) {
    return
  }

  const infoColor = resolveThemeColor('--color-info-500', '#1890ff')
  const successColor = resolveThemeColor('--color-success-500', '#52c41a')
  const errorColor = resolveThemeColor('--color-error-500', '#ff4d4f')
  const tertiaryColor = resolveThemeColor('--gray-400', '#979999')

  chartInstance.setOption({
    animationDuration: 400,
    color: [infoColor, successColor, errorColor, tertiaryColor],
    legend: {
      top: 0
    },
    grid: {
      top: 48,
      left: 24,
      right: 24,
      bottom: 32,
      containLabel: true
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      }
    },
    xAxis: {
      type: 'category',
      axisLabel: {
        interval: 0,
        rotate: kidStatRows.value.length > 4 ? 20 : 0
      },
      data: kidStatRows.value.map(item => item.kid || '-')
    },
    yAxis: {
      type: 'value',
      splitLine: {
        lineStyle: {
          type: 'dashed'
        }
      }
    },
    series: [
      {
        name: '加密请求',
        type: 'bar',
        barMaxWidth: 18,
        data: kidStatRows.value.map(item => buildKidChartBarData(item, item.encryptedRequests, infoColor))
      },
      {
        name: '解密成功',
        type: 'bar',
        barMaxWidth: 18,
        data: kidStatRows.value.map(item => buildKidChartBarData(item, item.decryptSuccess, successColor))
      },
      {
        name: '解密失败',
        type: 'bar',
        barMaxWidth: 18,
        data: kidStatRows.value.map(item => buildKidChartBarData(item, item.decryptFailure, errorColor))
      },
      {
        name: '加密响应',
        type: 'bar',
        barMaxWidth: 18,
        data: kidStatRows.value.map(item => buildKidChartBarData(item, item.encryptedResponses, tertiaryColor))
      }
    ]
  })
}

function resizeCharts() {
  failureReasonChartInstance?.resize()
  kidStatsChartInstance?.resize()
}

function destroyCharts() {
  if (failureReasonChartInstance) {
    failureReasonChartInstance.dispose()
    failureReasonChartInstance = null
  }
  if (kidStatsChartInstance) {
    kidStatsChartInstance.dispose()
    kidStatsChartInstance = null
  }
}

function clearFailureReasonSelection() {
  selectedFailureReason.value = ''
}

function clearKidSelection() {
  selectedKid.value = ''
}

function handleFailureReasonRowClick(row) {
  const targetReason = row?.reason || ''
  selectedFailureReason.value = selectedFailureReason.value === targetReason ? '' : targetReason
}

function handleKidStatRowClick(row) {
  const targetKid = row?.kid || ''
  selectedKid.value = selectedKid.value === targetKid ? '' : targetKid
}

function getFailureReasonRowClassName({ row }) {
  return selectedFailureReason.value && row.reason === selectedFailureReason.value ? 'selected-table-row' : ''
}

function getKidStatRowClassName({ row }) {
  return selectedKid.value && row.kid === selectedKid.value ? 'selected-table-row' : ''
}

watch(autoRefresh, () => {
  resetRefreshTimer()
})

watch(() => settingsStore.isDark, async () => {
  await nextTick()
  renderFailureReasonChart()
  renderKidStatsChart()
})

watch(selectedFailureReason, () => {
  nextTick(() => {
    renderFailureReasonChart()
  })
})

watch(selectedKid, () => {
  nextTick(() => {
    renderKidStatsChart()
  })
})

onMounted(() => {
  loadMonitorData()
  resetRefreshTimer()
  window.addEventListener('resize', resizeCharts)
})

onBeforeUnmount(() => {
  monitorGeneration += 1
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
  window.removeEventListener('resize', resizeCharts)
  if (globalLoadingOpen) {
    feedback.closeLoading()
    globalLoadingOpen = false
  }
  destroyCharts()
})
</script>

<style lang="scss" scoped>
.mb16 {
  margin-bottom: 16px;
}

.summary-card {
  height: 140px;
}

.summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.summary-title {
  color: var(--workspace-text);
  font-size: 14px;
}

.summary-value {
  color: var(--workspace-text);
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
}

.summary-desc {
  margin-top: 10px;
  color: var(--workspace-text-secondary);
  font-size: 13px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.compact-actions {
  justify-content: flex-end;
}

.card-subtitle {
  color: var(--workspace-text-secondary);
  font-size: 12px;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag-item {
  margin: 0;
}

.health-card {
  height: 100%;
}

.health-item {
  margin-bottom: 18px;
}

.health-item:last-of-type {
  margin-bottom: 24px;
}

.health-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: var(--workspace-text);
  font-size: 13px;
}

.chart-panel {
  width: 100%;
  height: 300px;
  margin-bottom: 16px;
}

.chart-empty {
  padding: 16px 0 8px;
}

.table-title {
  margin-bottom: 12px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  font-weight: 500;
}

:deep(.selected-table-row) {
  background: var(--workspace-main-soft);
}

@media (max-width: 768px) {
  .summary-card {
    height: auto;
  }

  .card-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .card-actions {
    width: 100%;
    justify-content: space-between;
  }

  .compact-actions {
    justify-content: space-between;
  }

  .chart-panel {
    height: 260px;
  }
}
</style>
