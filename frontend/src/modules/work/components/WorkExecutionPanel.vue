<script setup lang="ts">
import { computed } from 'vue'
import { CheckCircleOutlined, ClockCircleOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import type { WorkExecutionState, WorkPlanItem, WorkStep } from '../api/index.ts'
import { workExecutionLabel } from '../presentation.ts'

const props = defineProps<{
  plan: WorkPlanItem[]
  steps: WorkStep[]
  state: WorkExecutionState
  busy: boolean
}>()
const completed = computed(() => props.plan.filter((item) => item.status === 'completed').length)
const currentStep = computed(() =>
  [...props.steps].reverse().find((step) => step.status === 'running' || step.status === 'pending')
)
const labels: Record<string, string> = {
  pending: '等待执行',
  running: '正在执行',
  succeeded: '已完成',
  completed: '已完成',
  failed: '未完成',
  unknown: '结果待核实',
  cancelled: '已停止'
}
const names: Record<string, string> = {
  list_inputs: '查看任务资料',
  inspect_file: '识别文件结构',
  read_document: '读取文档',
  read_sheet: '读取表格',
  load_skill: '准备所需技能',
  run_python: '处理文件与数据',
  execute: '运行处理步骤',
  write_file: '整理文件内容',
  read_file: '读取工作文件',
  list_files: '查看工作文件',
  publish_outputs: '检查交付成果',
  request_input: '等待补充信息',
  write_todos: '更新任务计划',
  model: '分析任务',
  summary: '整理任务上下文',
  web_search: '搜索公开网页'
}
const name = (step: WorkStep) =>
  names[step.name] ?? (step.kind === 'model' ? '分析与整理' : '处理任务步骤')
const progress = computed(() =>
  currentStep.value
    ? name(currentStep.value)
    : props.busy
      ? '正在推进任务'
      : props.state === 'completed'
        ? '执行过程'
        : workExecutionLabel[props.state]
)
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}
function detail(step: WorkStep): string {
  const data = record(step.result?.data),
    error = record(step.result?.error)
  const query =
    step.name === 'web_search' && typeof data?.query === 'string' ? `查询：${data.query}` : ''
  const results =
    step.name === 'web_search' && Array.isArray(data?.results)
      ? `返回 ${data.results.length} 个搜索结果`
      : ''
  return [query, results, data?.stdout, data?.stderr, error?.message]
    .filter((value): value is string => typeof value === 'string' && !!value)
    .join('\n')
    .slice(0, 12000)
}
function code(step: WorkStep): string {
  const arguments_ = record(step.details.arguments)
  const value =
    step.name === 'run_python'
      ? arguments_?.code
      : step.name === 'execute'
        ? arguments_?.command
        : null
  return typeof value === 'string' ? value.slice(0, 40000) : ''
}
</script>
<template>
  <section v-if="busy || plan.length || steps.length" class="work-progress" aria-label="任务进度">
    <details :open="busy">
      <summary>
        <span class="work-progress__heading"
          ><LoadingOutlined v-if="busy" spin /><CheckCircleOutlined
            v-else-if="state === 'completed'"
          /><ClockCircleOutlined v-else />{{ progress }}</span
        ><span class="work-progress__count">{{
          plan.length ? `${completed} / ${plan.length}` : '执行记录'
        }}</span>
      </summary>
      <ol v-if="plan.length" class="work-progress__plan">
        <li v-for="(item, index) in plan" :key="index" :class="`is-${item.status}`">
          <CheckCircleOutlined v-if="item.status === 'completed'" /><LoadingOutlined
            v-else-if="item.status === 'in_progress'"
            spin
          /><span v-else class="work-progress__dot" /><span>{{ item.content }}</span>
        </li>
      </ol>
      <details v-if="steps.length" class="work-progress__steps" :open="busy">
        <summary>查看执行记录 · {{ steps.length }} 步</summary>
        <ol>
          <li v-for="step in steps" :key="step.id">
            <div>
              <strong
                >{{ name(step)
                }}<code v-if="step.kind === 'tool'" class="work-progress__tool">{{
                  step.name
                }}</code></strong
              ><span
                :class="{ 'is-error': step.status === 'failed' || step.status === 'unknown' }"
                >{{ labels[step.status] ?? '已记录' }}</span
              >
            </div>
            <p v-if="step.summary">{{ step.summary }}</p>
            <pre v-if="detail(step)">{{ detail(step) }}</pre>
            <details v-if="code(step)" class="work-progress__code">
              <summary>查看处理代码</summary>
              <pre>{{ code(step) }}</pre>
            </details>
          </li>
        </ol>
      </details>
    </details>
  </section>
</template>
<style scoped>
.work-progress {
  width: 100%;
  margin: 0 0 16px;
  color: var(--color-text-secondary);
  background: var(--bg-container);
  font-size: 13px;
  line-height: 1.7;
}
.work-progress summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
  padding: 8px 0;
  list-style: none;
}
.work-progress summary::-webkit-details-marker {
  display: none;
}
.work-progress__heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
}
.work-progress__count {
  flex-shrink: 0;
  font-size: 12px;
}
.work-progress__plan {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 8px 0 14px;
  list-style: none;
}
.work-progress__plan li {
  display: flex;
  align-items: baseline;
  gap: 9px;
  overflow-wrap: anywhere;
}
.work-progress__plan .is-completed {
  color: var(--color-text-secondary);
}
.work-progress__plan .is-completed > :first-child {
  color: var(--workspace-success, #389e0d);
}
.work-progress__plan .is-in_progress {
  color: var(--main-color);
}
.work-progress__dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin: 0 3px;
  flex-shrink: 0;
  border: 1px solid var(--gray-400);
  border-radius: 50%;
}
.work-progress__steps {
  border-top: 1px solid var(--gray-150);
}
.work-progress__steps > ol {
  max-height: 260px;
  overflow: auto;
  display: grid;
  gap: 14px;
  list-style: none;
  margin: 0;
  padding: 0 14px 14px;
}
.work-progress__steps li > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.work-progress__steps strong {
  color: var(--color-text);
  font-weight: 500;
}
.work-progress__tool {
  margin-left: 8px;
  color: var(--color-text-tertiary);
  font: 11px var(--workspace-font-mono);
}
.work-progress__steps p {
  margin: 5px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.work-progress__steps pre {
  margin: 6px 0 0;
  padding: 8px;
  border-radius: 6px;
  background: var(--gray-50);
  font: 12px/1.7 var(--workspace-font-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.work-progress__steps .is-error {
  color: var(--workspace-danger);
}
@media (max-width: 640px) {
  .work-progress {
    width: calc(100% - 24px);
  }
}
</style>
