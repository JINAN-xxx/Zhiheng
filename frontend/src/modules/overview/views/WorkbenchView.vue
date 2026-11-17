<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Alert as AAlert, Button as AButton } from 'ant-design-vue'
import {
  AppstoreOutlined,
  RobotOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  PlusOutlined,
  UploadOutlined,
  RightOutlined,
  LineChartOutlined,
  CheckCircleOutlined,
  WalletOutlined,
  WarningOutlined,
  ClockCircleOutlined,
  KeyOutlined
} from '@ant-design/icons-vue'
import {
  identity,
  canUseCapability,
  canUseModule,
  hasAnyPermission
} from '../../identity/public.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { getOverview } from '../api/index.ts'
import type { WorkbenchOverview, DailyCalls, WorkbenchAlert } from '../api/index.ts'
import {
  kindLabels,
  resourcePath,
  statusLabel,
  isHealthy,
  successRate,
  callChange,
  compactNumber,
  pointText,
  localTime,
  activityText
} from '../presentation.ts'
import WorkbenchTrend from '../components/WorkbenchTrend.vue'

const data = ref<WorkbenchOverview | null>(null),
  loading = ref(false),
  error = ref('')
const scope = ref<'mine' | 'platform'>('mine'),
  days = ref<7 | 30>(7),
  resourceFilter = ref('all')
const requests = new RequestScope()
const authority = computed(() =>
  JSON.stringify([identity.value?.id, identity.value?.is_admin, identity.value?.permissions])
)
const canPlatform = computed(() =>
  hasAnyPermission(identity.value, 'platform:overview:platform_stats')
)
const metrics = computed(() => data.value?.metrics)
const credits = computed(() => data.value?.credits)
const periodLabel = computed(() => `近 ${data.value?.period_days ?? days.value} 日`)
const filteredResources = computed(() =>
  (data.value?.recent ?? [])
    .filter((item) => resourceFilter.value === 'all' || item.kind === resourceFilter.value)
    .slice(0, 6)
)
const filterOptions = computed(() => [
  { key: 'all', label: '全部资源' },
  ...(['model', 'agent', 'knowledge', 'skill'] as const)
    .filter((kind) =>
      canUseModule(
        { model: 'models', agent: 'agents', knowledge: 'knowledge', skill: 'skills' }[kind]
      )
    )
    .map((kind) => ({ key: kind, label: kindLabels[kind] }))
])
const resourceIcons = {
  model: AppstoreOutlined,
  agent: RobotOutlined,
  knowledge: DatabaseOutlined,
  skill: ThunderboltOutlined,
  application: KeyOutlined
}
const peak = computed(() => {
  const points = data.value?.metrics?.trend ?? []
  return points.reduce<DailyCalls | null>(
    (best, item) => (!best || item.calls > best.calls ? item : best),
    null
  )
})
const greeting = computed(() => {
  const hour = Number(
    new Intl.DateTimeFormat('en', {
      timeZone: 'Asia/Shanghai',
      hour: 'numeric',
      hourCycle: 'h23'
    }).format(new Date())
  )
  return hour < 6 ? '夜深了' : hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好'
})
const shortcuts = computed(() =>
  [
    {
      key: 'chat',
      label: '开始对话',
      description: '使用模型处理问题',
      path: '/chat',
      icon: MessageOutlined,
      allowed: canUseCapability('chat', 'use')
    },
    {
      key: 'agents',
      label: '创建智能助手',
      description: '配置专属助手',
      path: '/agents/new',
      icon: PlusOutlined,
      allowed: canUseCapability('agents', 'create')
    },
    {
      key: 'skills',
      label: '上传技能',
      description: '导入 SKILL.md 或 ZIP 包',
      path: '/skills?action=upload',
      icon: UploadOutlined,
      allowed: canUseCapability('skills', 'create')
    },
    {
      key: 'knowledge',
      label: '创建知识库',
      description: '管理业务知识与资料',
      path: '/knowledge?action=create',
      icon: DatabaseOutlined,
      allowed: canUseCapability('knowledge', 'create')
    }
  ].filter((item) => item.allowed)
)
let loadedAt = 0
async function load() {
  const ticket = requests.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await getOverview(ticket.signal, scope.value, days.value)
    if (ticket.isCurrent()) {
      data.value = result
      loadedAt = Date.now()
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function setPeriod(value: 7 | 30) {
  if (days.value !== value) {
    days.value = value
    void load()
  }
}
function setScope(value: 'mine' | 'platform') {
  if (scope.value !== value) {
    scope.value = value
    data.value = null
    void load()
  }
}
function alertPath(item: WorkbenchAlert): string | null {
  return item.kind && item.resource_id
    ? resourcePath(item.kind, item.resource_id)
    : canUseModule('usage')
      ? '/usage?tab=history'
      : null
}
function focused() {
  if (document.visibilityState === 'visible' && Date.now() - loadedAt > 30000) void load()
}
watch(
  authority,
  () => {
    data.value = null
    resourceFilter.value = 'all'
    scope.value = canPlatform.value ? 'platform' : 'mine'
    void load()
  },
  { immediate: true }
)
onMounted(() => window.addEventListener('focus', focused))
onBeforeUnmount(() => {
  requests.dispose()
  window.removeEventListener('focus', focused)
})
</script>

<template>
  <section class="workbench" aria-label="工作台" :aria-busy="loading">
    <header class="workbench-hero">
      <div>
        <h1>{{ greeting }}，{{ identity?.username }}</h1>
        <p>这里是你的 AI 资源与运行情况总览。</p>
      </div>
      <div class="hero-actions">
        <RefreshButton label="刷新工作台" :loading="loading" @click="load" /><RouterLink
          v-if="canUseCapability('agents', 'create')"
          class="hero-button"
          to="/agents/new"
          ><PlusOutlined />创建助手</RouterLink
        ><RouterLink
          v-if="canUseCapability('chat', 'use')"
          class="hero-button hero-button--primary"
          to="/chat"
          ><MessageOutlined />开始对话</RouterLink
        >
      </div>
    </header>
    <div v-if="error" class="workbench-error">
      <AAlert type="warning" :message="error" show-icon />
      <AButton size="small" @click="load">重试</AButton>
    </div>
    <nav v-if="shortcuts.length" class="workbench-shortcuts panel" aria-label="快捷入口">
      <RouterLink v-for="item in shortcuts" :key="item.key" :to="item.path"
        ><span class="shortcut-icon"><component :is="item.icon" /></span
        ><span
          ><strong>{{ item.label }}</strong
          ><small>{{ item.description }}</small></span
        ><RightOutlined
      /></RouterLink>
    </nav>
    <div class="workbench-scope">
      <span>{{ data?.scope === 'platform' ? '全平台运行统计' : '我的运行统计' }}</span>
      <div v-if="canPlatform" class="segmented" aria-label="统计范围">
        <button :aria-pressed="scope === 'mine'" @click="setScope('mine')">仅本人</button
        ><button :aria-pressed="scope === 'platform'" @click="setScope('platform')">全平台</button>
      </div>
    </div>
    <section class="workbench-metrics" aria-label="运行指标">
      <article class="metric panel">
        <span class="metric-label"
          ><span class="metric-icon violet"><LineChartOutlined /></span>今日模型调用</span
        ><strong>{{ metrics ? metrics.today_calls.toLocaleString() : '—' }}</strong
        ><small :class="{ positive: metrics && metrics.today_calls > metrics.yesterday_calls }">{{
          metrics
            ? callChange(metrics.today_calls, metrics.yesterday_calls)
            : loading
              ? '正在读取…'
              : '未开放用量统计'
        }}</small>
      </article>
      <article class="metric panel">
        <span class="metric-label"
          ><span class="metric-icon green"><CheckCircleOutlined /></span>调用成功率</span
        ><strong>{{
          metrics ? successRate(metrics.successful_calls, metrics.failed_calls) : '—'
        }}</strong
        ><small>{{
          metrics ? periodLabel + ' · 已结束调用' : loading ? '正在读取…' : '未开放用量统计'
        }}</small>
      </article>
      <article class="metric panel">
        <span class="metric-label"
          ><span class="metric-icon blue"><ThunderboltOutlined /></span>Token 消耗</span
        ><strong :title="metrics?.today_known_tokens.toLocaleString()">{{
          metrics ? compactNumber(metrics.today_known_tokens) : '—'
        }}</strong
        ><small>{{
          metrics
            ? '今日已确认' +
              (metrics.today_unconfirmed_calls
                ? ' · ' + metrics.today_unconfirmed_calls + ' 次Token未完整确认'
                : '')
            : loading
              ? '正在读取…'
              : '未开放用量统计'
        }}</small>
      </article>
      <article class="metric panel">
        <span class="metric-label"
          ><span class="metric-icon orange"><WalletOutlined /></span>本月积分消耗</span
        ><strong :title="credits?.monthly_points"
          >{{ credits ? pointText(credits.monthly_points) : '—'
          }}<em v-if="credits">积分</em></strong
        ><small>{{
          credits
            ? !credits.enabled
              ? '积分计费已关闭'
              : credits.unconfirmed_calls
                ? credits.unconfirmed_calls + ' 次用量待核对'
                : '已结算消耗'
            : loading
              ? '正在读取…'
              : '未开放积分统计'
        }}</small>
      </article>
    </section>
    <div class="workbench-grid">
      <section class="panel trend-panel" aria-label="调用趋势">
        <header class="panel-heading">
          <div>
            <h2>调用趋势</h2>
            <span>{{ data?.scope === 'platform' ? '全平台调用量' : '我的调用量' }}</span>
          </div>
          <div class="segmented" aria-label="趋势周期">
            <button :aria-pressed="days === 7" @click="setPeriod(7)">近7天</button
            ><button :aria-pressed="days === 30" @click="setPeriod(30)">近30天</button>
          </div>
        </header>
        <WorkbenchTrend v-if="data?.metrics" :points="data.metrics.trend" />
        <div v-else class="panel-empty trend-empty">
          <LineChartOutlined /><strong>{{
            loading ? '正在读取调用趋势' : '暂无可展示的用量统计'
          }}</strong
          ><span v-if="!loading">开放用量与调用权限后可查看。</span>
        </div>
        <footer class="trend-footer">
          <span><i />调用次数</span
          ><span v-if="peak && peak.calls"
            >峰值 <b>{{ peak.calls.toLocaleString() }}</b> 次 ·
            {{ peak.day.slice(5).replace('-', '月') }}日</span
          ><span v-else>有模型调用后将自动记录趋势</span>
        </footer>
      </section>
      <section class="panel alerts-panel" aria-label="运行提醒">
        <header class="panel-heading">
          <div>
            <h2>运行提醒</h2>
            <span v-if="data?.alerts.length" class="alert-count">{{ data.alerts.length }}</span>
          </div>
          <span>我的资源</span>
        </header>
        <div v-if="data?.alerts.length" class="alert-list">
          <component
            :is="alertPath(item) ? RouterLink : 'div'"
            v-for="item in data.alerts.slice(0, 4)"
            :key="item.id"
            :to="alertPath(item) || undefined"
            class="alert-row"
            ><span class="notice-icon" :class="item.level"
              ><WarningOutlined v-if="item.level === 'error'" /><KeyOutlined
                v-else-if="item.level === 'warning'" /><ClockCircleOutlined v-else
            /></span>
            <div>
              <strong>{{ item.title }}</strong>
              <p>
                {{ item.description
                }}<template v-if="item.level === 'warning' && item.occurred_at">
                  {{ localTime(item.occurred_at) }} 到期</template
                >
              </p>
            </div>
            <RightOutlined v-if="alertPath(item)"
          /></component>
        </div>
        <div v-else class="panel-empty">
          <CheckCircleOutlined class="empty-check" /><strong>{{
            loading ? '正在读取运行提醒' : '当前暂无运行提醒'
          }}</strong
          ><span>资料处理状态及 API Key 临期提醒会显示在这里。</span>
        </div>
      </section>
      <section class="panel recent-panel" aria-label="最近调用资源">
        <header class="panel-heading resource-heading">
          <h2>最近调用</h2>
          <div class="resource-filters" aria-label="资源类型">
            <button
              v-for="option in filterOptions"
              :key="option.key"
              :aria-pressed="resourceFilter === option.key"
              @click="resourceFilter = option.key"
            >
              {{ option.label }}
            </button>
          </div>
          <RouterLink v-if="canUseModule('usage')" to="/usage?tab=history" class="subtle-link"
            >调用记录<RightOutlined
          /></RouterLink>
        </header>
        <div class="resource-table-scroll">
          <table v-if="filteredResources.length" class="resource-table">
            <thead>
              <tr>
                <th>资源名称</th>
                <th>类型</th>
                <th>最近调用</th>
                <th>当前状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filteredResources" :key="item.kind + item.id">
                <td>
                  <RouterLink :to="resourcePath(item.kind, item.id)"
                    ><span class="resource-icon"><component :is="resourceIcons[item.kind]" /></span
                    ><span :title="item.name">{{ item.name }}</span></RouterLink
                  >
                </td>
                <td>{{ kindLabels[item.kind] }}</td>
                <td>{{ localTime(item.occurred_at) }}</td>
                <td>
                  <span class="resource-status" :class="{ 'is-healthy': isHealthy(item.status) }"
                    ><i />{{ statusLabel(item.status) }}</span
                  >
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="panel-empty">
            <AppstoreOutlined /><strong>{{
              loading ? '正在读取最近调用' : '还没有可展示的调用资源'
            }}</strong
            ><span>这里只展示本人调用过且当前有权访问的资源。</span>
          </div>
        </div>
      </section>
      <section class="panel activity-panel" aria-label="资源动态">
        <header class="panel-heading">
          <h2>资源动态</h2>
          <span>我的资源</span>
        </header>
        <div v-if="data?.activities.length" class="activity-list">
          <RouterLink
            v-for="(item, index) in data.activities.slice(0, 4)"
            :key="item.kind + item.id + item.occurred_at + index"
            :to="resourcePath(item.kind, item.id)"
            class="activity-row"
            ><span class="activity-icon"><component :is="resourceIcons[item.kind]" /></span>
            <div>
              <strong>{{ activityText(item) }}</strong
              ><small>{{ localTime(item.occurred_at) }} · {{ identity?.username }}</small>
            </div>
            <RightOutlined
          /></RouterLink>
        </div>
        <div v-else class="panel-empty">
          <ClockCircleOutlined /><strong>{{ loading ? '正在读取资源动态' : '暂无资源动态' }}</strong
          ><span>创建资源、启用助手或处理资料后会显示动态。</span>
        </div>
      </section>
    </div>
    <footer class="workbench-footnote">
      <span>按北京时间汇总。模型调用包含回复、摘要、向量及重排；未知用量单独标注。</span
      ><span v-if="data">更新于 {{ localTime(data.observed_at) }}</span>
    </footer>
  </section>
</template>

<style scoped>
.workbench {
  padding: 26px 32px 22px;
  min-width: 0;
  min-height: 100%;
  background: var(--bg-page);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.workbench-hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  margin-bottom: 22px;
}
.workbench-hero h1 {
  margin: 0;
  font-size: 26px;
  line-height: 1.4;
  font-weight: 650;
  letter-spacing: -0.4px;
}
.workbench-hero p {
  margin: 5px 0 0;
  font-size: 13px;
  color: var(--color-text-tertiary);
}
.hero-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}
.hero-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 38px;
  padding: 0 16px;
  border: 1px solid #dfe2ed;
  border-radius: 9px;
  background: white;
  color: var(--color-text);
  font-size: 13px;
  white-space: nowrap;
}
.hero-button--primary {
  background: var(--main-color);
  border-color: var(--main-color);
  color: white;
  box-shadow: 0 4px 10px #6655ff20;
}
.hero-button:hover {
  border-color: var(--main-color);
}
.hero-button--primary:hover {
  color: white;
  filter: brightness(1.05);
}
.panel {
  background: var(--bg-container);
  border: 1px solid #e5e8f2;
  border-radius: 14px;
  min-width: 0;
}
.workbench-error {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.workbench-error :deep(.ant-alert) {
  flex: 1;
  min-width: 0;
}
.workbench a {
  text-decoration: none;
}

.workbench-shortcuts {
  display: grid;
  grid-auto-columns: minmax(0, 1fr);
  grid-auto-flow: column;
  margin-bottom: 17px;
  padding: 16px 0;
}
.workbench-shortcuts a {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  min-width: 0;
  color: var(--color-text);
  border-right: 1px solid #e8eaf2;
}
.workbench-shortcuts a:last-child {
  border-right: 0;
}
.shortcut-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  border-radius: 10px;
  color: var(--main-color);
  background: #f4f2ff;
  font-size: 19px;
}
.workbench-shortcuts a > span:nth-child(2) {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.workbench-shortcuts strong {
  font-size: 13px;
  font-weight: 550;
  white-space: nowrap;
}
.workbench-shortcuts small {
  font-size: 11px;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}
.workbench-shortcuts a > .anticon {
  font-size: 11px;
  color: #b2b7c7;
  margin-left: auto;
}
.workbench-scope {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.segmented {
  display: inline-flex;
  background: #f2f3f8;
  padding: 3px;
  border-radius: 8px;
  gap: 2px;
}
.segmented button {
  border: 0;
  min-height: 27px;
  padding: 3px 10px;
  background: transparent;
  color: #8990a5;
  font: inherit;
  font-size: 12px;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.segmented button[aria-pressed='true'] {
  background: white;
  color: #434a61;
  box-shadow: 0 1px 4px #1d234b0b;
}
.workbench-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}
.metric {
  padding: 16px 18px 14px;
  display: grid;
  gap: 4px;
  min-height: 112px;
}
.metric-label {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #787f96;
  font-size: 12px;
}
.metric-icon {
  display: grid;
  place-items: center;
  width: 27px;
  height: 27px;
  border-radius: 8px;
  font-size: 15px;
}
.violet {
  color: #6c5cff;
  background: #f2efff;
}
.green {
  color: #00a68b;
  background: #eaf8f3;
}
.blue {
  color: #5481db;
  background: #edf3ff;
}
.orange {
  color: #d89939;
  background: #fff5e6;
}
.metric > strong {
  font-size: 29px;
  line-height: 1.35;
  letter-spacing: -0.5px;
  font-weight: 650;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.metric > strong em {
  font-style: normal;
  font-size: 11px;
  font-weight: 400;
  color: #9198ac;
  margin-left: 7px;
  letter-spacing: 0;
}
.metric > small {
  text-align: right;
  font-size: 11px;
  color: #9299ad;
  min-height: 16px;
}
.metric > small.positive {
  color: #009b83;
}
.workbench-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(290px, 1fr);
  gap: 16px;
}
.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 14px;
  min-height: 58px;
}
.panel-heading h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}
.panel-heading > div:first-child {
  display: flex;
  align-items: center;
  gap: 10px;
}
.panel-heading span,
.subtle-link {
  font-size: 11px;
  color: #9299ad;
}
.trend-panel {
  padding-bottom: 13px;
}
.trend-panel > .workbench-chart {
  padding: 0 12px;
}
.trend-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid #f0f1f6;
  padding: 13px 20px 0;
  font-size: 11px;
  color: #8e95a9;
}
.trend-footer > span:first-child {
  display: flex;
  align-items: center;
  gap: 7px;
}
.trend-footer i {
  width: 15px;
  height: 3px;
  border-radius: 3px;
  background: #6c5cff;
}
.trend-footer b {
  color: #525a72;
  font-weight: 500;
}
.alerts-panel > .panel-heading,
.activity-panel > .panel-heading,
.recent-panel > .panel-heading {
  border-bottom: 1px solid #edf0f6;
}
.alert-count {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #fff5e6;
  color: #d28e31 !important;
}
.alert-list {
  padding: 0 20px;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 17px 0;
  border-bottom: 1px solid #f0f1f6;
  color: var(--color-text);
  min-width: 0;
}
.alert-row:last-child {
  border: 0;
}
.notice-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 10px;
  font-size: 17px;
}
.notice-icon.error {
  background: #fff0f2;
  color: #ef6276;
}
.notice-icon.warning {
  background: #fff5e7;
  color: #cf8d3a;
}
.notice-icon.info {
  background: #edf4ff;
  color: #5a88dd;
}
.alert-row > div {
  min-width: 0;
  flex: 1;
}
.alert-row strong {
  font-size: 12px;
  font-weight: 500;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.alert-row p {
  margin: 5px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: #939aaf;
}
.alert-row > .anticon {
  font-size: 11px;
  color: #a3a9ba;
}
.panel-empty {
  display: flex;
  min-height: 190px;
  padding: 28px 22px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  text-align: center;
  color: #929aaf;
}
.panel-empty > .anticon {
  font-size: 27px;
  color: #aaa2ed;
}
.panel-empty > .empty-check {
  color: #73b4a5;
}
.panel-empty strong {
  font-size: 13px;
  font-weight: 400;
  color: #697189;
}
.panel-empty > span {
  font-size: 11px;
  line-height: 1.7;
  max-width: 290px;
}
.trend-empty {
  height: 236px;
}
.resource-heading {
  justify-content: flex-start;
  padding-bottom: 0;
  padding-top: 0;
  min-height: 60px;
}
.resource-filters {
  display: flex;
  gap: 16px;
  align-self: stretch;
  margin-left: 8px;
  min-width: 0;
  overflow-x: auto;
}
.resource-filters button {
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  border: 0;
  border-bottom: 2px solid transparent;
  padding: 0 0;
  background: transparent;
  color: #9097aa;
  cursor: pointer;
}
.resource-filters button[aria-pressed='true'] {
  color: var(--main-color);
  border-bottom-color: var(--main-color);
}
.subtle-link {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  white-space: nowrap;
}
.resource-table-scroll {
  padding: 0 20px 10px;
  overflow-x: auto;
}
.resource-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 11px;
  table-layout: fixed;
}
.resource-table th {
  font-weight: 400;
  color: #969db0;
  text-align: left;
  padding: 12px 0 9px;
}
.resource-table th:first-child {
  width: 38%;
}
.resource-table th:nth-child(2) {
  width: 16%;
}
.resource-table th:nth-child(3) {
  width: 25%;
}
.resource-table td {
  border-top: 1px solid #f0f1f6;
  padding: 10px 0;
  color: #7a8298;
  white-space: nowrap;
}
.resource-table td > a {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #4e566c;
  min-width: 0;
  padding-right: 12px;
}
.resource-table td > a > span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
}
.resource-icon {
  width: 27px;
  height: 27px;
  flex-shrink: 0;
  border-radius: 7px;
  display: grid;
  place-items: center;
  background: #f4f2ff;
  color: #7565f5;
  font-size: 14px;
}
.resource-status {
  display: flex;
  gap: 6px;
  align-items: center;
}
.resource-status i {
  height: 6px;
  width: 6px;
  border-radius: 50%;
  background: #d7a144;
  flex-shrink: 0;
}
.resource-status.is-healthy i {
  background: #129783;
}
.activity-list {
  padding: 0 20px;
}
.activity-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 68px;
  padding: 12px 0;
  color: #5a6278;
}
.activity-row:not(:last-child):after {
  content: '';
  position: absolute;
  left: 15px;
  top: 46px;
  bottom: -12px;
  width: 1px;
  background: #eef0f5;
}
.activity-icon {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #f4f5f9;
  color: #9299ae;
  font-size: 15px;
  z-index: 1;
}
.activity-row > div {
  min-width: 0;
  flex: 1;
}
.activity-row strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 400;
}
.activity-row small {
  display: block;
  font-size: 11px;
  color: #979fb3;
  margin-top: 6px;
}
.activity-row > .anticon {
  font-size: 11px;
  color: #acb2c2;
}
.workbench-footnote {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 14px;
  font-size: 10px;
  color: #969db0;
  line-height: 1.7;
}
.workbench a:focus-visible,
.workbench button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 3px;
}
.workbench a:hover strong {
  color: var(--main-color);
}
@media (min-width: 1800px) {
  .workbench {
    max-width: 1680px;
    margin: auto;
  }
}
@media (max-width: 1200px) {
  .workbench {
    padding: 22px;
  }
  .workbench-grid {
    grid-template-columns: minmax(0, 1.65fr) minmax(270px, 1fr);
  }
  .workbench-shortcuts a {
    padding: 0 14px;
    gap: 8px;
  }
  .workbench-shortcuts small {
    font-size: 10px;
  }
  .resource-filters {
    gap: 10px;
    margin-left: 0;
  }
  .metric {
    padding: 14px;
  }
  .metric > strong {
    font-size: 26px;
  }
}
@media (max-width: 1050px) {
  .workbench-grid {
    grid-template-columns: 1fr;
  }
  .workbench-metrics {
    gap: 12px;
  }
  .alerts-panel .panel-empty {
    min-height: 150px;
  }
  .alert-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0 20px;
  }
  .resource-heading {
    flex-wrap: wrap;
  }
  .workbench-hero h1 {
    font-size: 24px;
  }
}
@media (max-width: 700px) {
  .workbench {
    padding: 18px 14px;
  }
  .workbench-hero {
    align-items: flex-start;
    flex-direction: column;
    gap: 13px;
    margin-bottom: 16px;
  }
  .hero-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .workbench-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .workbench-shortcuts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-auto-flow: row;
    padding: 4px 0;
  }
  .workbench-shortcuts a {
    padding: 13px 12px;
  }
  .workbench-shortcuts a:nth-child(2) {
    border-right: 0;
  }
  .workbench-shortcuts a > .anticon {
    display: none;
  }
  .workbench-shortcuts strong {
    font-size: 12px;
  }
  .shortcut-icon {
    height: 33px;
    width: 33px;
  }
  .metric > strong {
    font-size: 25px;
  }
  .metric > small {
    font-size: 10px;
    text-align: left;
  }
  .panel-heading {
    padding: 16px;
  }
  .alert-list {
    grid-template-columns: 1fr;
    padding: 0 16px;
  }
  .trend-footer {
    padding-left: 16px;
    padding-right: 16px;
  }
  .resource-heading {
    gap: 10px;
    padding-bottom: 0;
  }
  .resource-filters {
    gap: 15px;
    height: 40px;
    order: 3;
    width: 100%;
  }
  .resource-table-scroll {
    padding: 0 14px 8px;
  }
  .resource-table th:nth-child(2),
  .resource-table td:nth-child(2) {
    display: none;
  }
  .resource-table th:first-child {
    width: 42%;
  }
  .resource-table th:nth-child(3) {
    width: 31%;
  }
  .activity-list {
    padding: 0 16px;
  }
  .workbench-footnote {
    font-size: 10px;
  }
  .hero-button {
    height: 35px;
    padding: 0 12px;
  }
  .workbench-grid {
    gap: 14px;
  }
}
</style>
