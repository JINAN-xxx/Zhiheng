<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Empty as AEmpty,
  Progress as AProgress,
  Spin as ASpin,
  Tag as ATag
} from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getUsage } from '../api/index.ts'
import type { UsageSummary } from '../api/index.ts'
const summary = ref<UsageSummary | null>(null)
const error = ref('')
const loading = ref(false)
const scope = new RequestScope()
const percent = computed(() =>
  summary.value && summary.value.limit_tokens > 0
    ? Math.min(
        100,
        Math.round(
          ((summary.value.used_tokens + summary.value.reserved_tokens) /
            summary.value.limit_tokens) *
            100
        )
      )
    : 0
)
const tokens = (value: number | null): string =>
  value === null ? '待核对' : value.toLocaleString()
const recordStatus = (status: string): string =>
  ({ known: '已核算', unknown: '待核对', pending: '待核算' })[status] ?? status
onBeforeUnmount(() => {
  scope.dispose()
})
onMounted(() => {
  void load()
})
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await getUsage(context.signal)
    if (context.isCurrent()) summary.value = result
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
</script>
<template>
  <section class="content-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">USAGE OVERVIEW</p>
        <h1>用量记录</h1>
        <p class="muted">了解每一次调用，掌握你的资源使用。</p>
      </div>
      <AButton :loading="loading" @click="load"><ReloadOutlined /> 刷新</AButton>
    </div>
    <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" /><ASpin
      :spinning="loading"
      ><template v-if="summary"
        ><div class="usage-stats">
          <article>
            <span>已使用</span
            ><strong>{{ tokens(summary.used_tokens) }}<small>tokens</small></strong>
          </article>
          <article>
            <span>预留中</span
            ><strong>{{ tokens(summary.reserved_tokens) }}<small>tokens</small></strong>
          </article>
          <article>
            <span>配额上限</span
            ><strong>{{ tokens(summary.limit_tokens) }}<small>tokens</small></strong>
          </article>
        </div>
        <div class="quota-bar">
          <span class="muted small">已用与预留占比</span
          ><AProgress :percent="percent" :stroke-color="'var(--color-primary)'" />
        </div>
        <section class="settings-section">
          <div class="section-title">
            <h2>调用明细</h2>
            <span class="muted small">{{ summary.records.length }} 条记录</span>
          </div>
          <div v-if="summary.records.length" class="table-scroll">
            <table class="usage-table">
              <thead>
                <tr>
                  <th>时间 / 运行</th>
                  <th>来源</th>
                  <th>状态</th>
                  <th>输入 tokens</th>
                  <th>输出 tokens</th>
                  <th>计入用量</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="record in summary.records" :key="record.id">
                  <td>
                    <time>{{ new Date(record.created_at).toLocaleString() }}</time
                    ><small class="record-id" :title="record.run_id">{{
                      record.run_id.slice(0, 8)
                    }}</small>
                  </td>
                  <td>
                    {{
                      record.source === 'connection_test'
                        ? '连接测试'
                        : record.source === 'chat'
                          ? 'AI 对话'
                          : record.source
                    }}
                  </td>
                  <td>
                    <ATag :color="record.status === 'known' ? 'success' : 'warning'">{{
                      recordStatus(record.status)
                    }}</ATag>
                  </td>
                  <td>{{ tokens(record.input_tokens) }}</td>
                  <td>{{ tokens(record.output_tokens) }}</td>
                  <td>
                    {{ record.status === 'unknown' ? '待核对' : tokens(record.charged_tokens) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <AEmpty v-else description="还没有调用记录" :image="AEmpty.PRESENTED_IMAGE_SIMPLE" />
          <p class="muted small">预留用量会在运行完成后结算；未知用量将显示为待核对。</p>
        </section></template
      ></ASpin
    >
  </section>
</template>
