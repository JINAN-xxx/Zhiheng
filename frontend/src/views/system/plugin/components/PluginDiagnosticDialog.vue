<template>
  <ResourceDrawer :open="modelValue" :title="title" :width="920" destroy-on-close @close="emit('update:modelValue', false)">
    <a-alert v-if="result.message" :message="result.message" :type="result.ok ? 'success' : 'warning'" show-icon class="mb16" />
    <a-skeleton v-if="loading" :paragraph="{ rows: 6 }" active />
    <template v-else>
      <div class="diagnostic-summary">
        <div class="diagnostic-summary-item"><span class="diagnostic-summary-label">插件ID</span><span class="diagnostic-summary-value">{{ result.pluginId || '-' }}</span></div>
        <div class="diagnostic-summary-item"><span class="diagnostic-summary-label">结果</span><a-tag :color="result.ok ? 'green' : 'red'">{{ result.ok ? '正常' : '异常' }}</a-tag></div>
        <div v-if="health" class="diagnostic-summary-item"><span class="diagnostic-summary-label">状态</span><span class="diagnostic-summary-value">{{ health.status || '-' }}</span></div>
        <div v-if="health" class="diagnostic-summary-item"><span class="diagnostic-summary-label">耗时</span><span class="diagnostic-summary-value">{{ health.durationMs ?? '-' }}ms</span></div>
      </div>
      <a-tabs>
        <a-tab-pane v-if="health" key="health" tab="健康状态">
          <a-descriptions :column="2" bordered class="mb16"><a-descriptions-item label="检查器">{{ health.checker || '-' }}</a-descriptions-item><a-descriptions-item label="状态">{{ health.status || '-' }}</a-descriptions-item><a-descriptions-item label="说明" :span="2">{{ health.message || '-' }}</a-descriptions-item><a-descriptions-item label="错误" :span="2">{{ health.error || '-' }}</a-descriptions-item></a-descriptions>
          <a-textarea :value="formatJson(health.details)" :rows="8" readonly />
        </a-tab-pane>
        <a-tab-pane v-if="diagnose" key="summary" tab="检查摘要">
          <a-descriptions :column="3" bordered class="mb16"><a-descriptions-item label="依赖">{{ formatBoolean(diagnose.check?.dependencyOk) }}</a-descriptions-item><a-descriptions-item label="Manifest">{{ formatBoolean(diagnose.check?.manifestOk) }}</a-descriptions-item><a-descriptions-item label="结构">{{ formatBoolean(diagnose.check?.structureOk) }}</a-descriptions-item><a-descriptions-item label="插件依赖">{{ formatBoolean(diagnose.check?.pluginDependencyOk) }}</a-descriptions-item><a-descriptions-item label="菜单冲突">{{ formatBoolean(diagnose.check?.menuConflictOk) }}</a-descriptions-item><a-descriptions-item label="配置项">{{ diagnose.config?.summary?.total ?? '-' }}</a-descriptions-item></a-descriptions>
          <a-table :columns="validationColumns" :data-source="validationItems" :pagination="false" row-key="value" size="small" :scroll="{ x: 900 }"><template #bodyCell="{ column, record }"><a-tag v-if="column.key === 'level'" :color="tagColor(getValidationLevelTagType(record.level))">{{ getValidationLevelLabel(record.level) }}</a-tag></template></a-table>
        </a-tab-pane>
        <a-tab-pane v-if="diagnose" key="menu" tab="菜单计划"><a-textarea :value="formatJson(diagnose.menuPlan)" :rows="12" readonly /></a-tab-pane>
        <a-tab-pane key="raw" tab="原始数据"><a-textarea :value="formatJson(result)" :rows="14" readonly /></a-tab-pane>
      </a-tabs>
    </template>
    <template #footer><a-button @click="emit('update:modelValue', false)">关 闭</a-button></template>
  </ResourceDrawer>
</template>

<script setup name="PluginDiagnosticDialog">
import { computed } from "vue";
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: "插件诊断"
  },
  result: {
    type: Object,
    default: () => ({})
  },
  loading: {
    type: Boolean,
    default: false
  },
  formatJson: {
    type: Function,
    required: true
  },
  formatBoolean: {
    type: Function,
    required: true
  },
  getValidationLevelLabel: {
    type: Function,
    required: true
  },
  getValidationLevelTagType: {
    type: Function,
    required: true
  }
});

const emit = defineEmits(["update:modelValue"]);
const validationColumns = [
  { title: '分类', dataIndex: 'category', key: 'category', width: 120, align: 'center' }, { title: '等级', key: 'level', width: 90, align: 'center' },
  { title: '类型', dataIndex: 'kind', key: 'kind', width: 170, ellipsis: true }, { title: '对象', dataIndex: 'value', key: 'value', width: 180, ellipsis: true },
  { title: '说明', dataIndex: 'message', key: 'message', width: 260, ellipsis: true },
];
const tagColor = type => ({ success: 'green', warning: 'orange', danger: 'red', info: 'default', primary: 'blue' }[type] || 'default');

const health = computed(() => props.result.health || null);
const diagnose = computed(() => props.result.info || props.result.check ? props.result : null);

const validationItems = computed(() => {
  const check = diagnose.value?.check || {};
  const groups = [
    ["依赖", check.dependencies],
    ["Manifest 错误", check.manifestIssues],
    ["Manifest 警告", check.manifestWarnings],
    ["插件依赖", check.pluginDependencyErrors],
    ["结构", check.structureErrors],
    ["菜单冲突", check.menuConflicts]
  ];

  return groups.flatMap(([category, items]) =>
    Array.isArray(items)
      ? items.map(item => ({
          category,
          level: item.level || (category.includes("警告") ? "warning" : "error"),
          kind: item.kind || item.name || item.status || "-",
          value: item.value || item.requirement || item.path || item.pluginId || item.perms || "-",
          message: item.message || item.suggestion || item.reason || ""
        }))
      : []
  );
});
</script>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}

.diagnostic-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.diagnostic-summary-item {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--workspace-border);
  border-radius: 4px;
  background: var(--workspace-surface-muted);
}

.diagnostic-summary-label {
  display: block;
  margin-bottom: 6px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
}

.diagnostic-summary-value {
  color: var(--workspace-text);
  font-size: 14px;
  font-weight: 600;
  word-break: break-all;
}
</style>
