<template>
   <ResourceDrawer title="插件依赖" :open="modelValue" :width="860" destroy-on-close @close="emit('update:modelValue', false)">
      <a-alert v-if="result.message" :message="result.message" :type="result.ok ? 'success' : 'warning'" show-icon class="mb16" />
      <div class="dependency-summary">
         <div class="dependency-summary-item">
            <span class="dependency-summary-label">插件ID</span>
            <span class="dependency-summary-value">{{ result.pluginId || "-" }}</span>
         </div>
         <div class="dependency-summary-item">
            <span class="dependency-summary-label">依赖检查</span>
            <a-tag :color="result.dependencyOk ? 'green' : 'red'">{{ formatBoolean(result.dependencyOk) }}</a-tag>
         </div>
         <div class="dependency-summary-item">
            <span class="dependency-summary-label">缺失依赖</span>
            <span class="dependency-summary-value is-danger">{{ dependencyProblemCount }}</span>
         </div>
         <div class="dependency-summary-item">
            <span class="dependency-summary-label">安装计划</span>
            <span class="dependency-summary-value">{{ result.planCount ?? (result.plan || []).length }}</span>
         </div>
         <div class="dependency-summary-item">
            <span class="dependency-summary-label">安装策略</span>
            <a-tag v-if="policy" :color="tagColor(policyTagType)">{{ policy.mode || "-" }}</a-tag>
            <span v-else class="dependency-summary-value">-</span>
         </div>
      </div>
      <a-alert
         v-if="policy"
         :message="policySummaryTitle"
         :type="policyAllowed ? 'success' : 'warning'"
         show-icon
         class="mb16"
      >
         <template #description>
            <div v-if="policyMessages.length" class="dependency-policy-messages">
               <div v-for="message in policyMessages" :key="message">{{ message }}</div>
            </div>
            <div v-if="policyNextStep" class="dependency-policy-next">{{ policyNextStep }}</div>
         </template>
      </a-alert>
      <a-tabs>
         <a-tab-pane key="dependencies" tab="依赖状态"><a-table :columns="dependencyColumns" :data-source="result.dependencies || []" :pagination="false" row-key="requirement" size="small" :scroll="{ x: 900 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'installedVersion'">{{ record.installedVersion || '-' }}</span><a-tag v-else-if="column.key === 'installed'" :color="tagColor(getDependencyTagType(record))">{{ formatDependencyInstalled(record) }}</a-tag><a-tag v-else-if="column.key === 'satisfied'" :color="tagColor(getDependencyTagType(record))">{{ formatDependencyVersionSatisfied(record) }}</a-tag></template></a-table></a-tab-pane>
         <a-tab-pane key="plan" tab="安装计划"><a-table :columns="planColumns" :data-source="result.plan || []" :pagination="false" row-key="requirement" size="small" :scroll="{ x: 800 }"><template #bodyCell="{ column, record }"><div v-if="column.key === 'command'" class="dependency-command-cell"><span class="dependency-command-text">{{ record.commandText || '-' }}</span><a-tooltip title="复制命令"><a-button type="link" :disabled="!record.commandText" @click="copyDependencyCommand(record.commandText)"><template #icon><CopyOutlined /></template></a-button></a-tooltip></div></template></a-table></a-tab-pane>
         <a-tab-pane v-if="policy" key="policy" tab="策略判定"><a-descriptions :column="2" bordered size="small" class="mb16"><a-descriptions-item label="策略模式">{{ policy.mode || '-' }}</a-descriptions-item><a-descriptions-item label="允许执行"><a-tag :color="policyAllowed ? 'green' : 'red'">{{ policyAllowed ? '允许' : '阻断' }}</a-tag></a-descriptions-item><a-descriptions-item label="阻断原因">{{ formatPolicyList(policy.reasons) }}</a-descriptions-item><a-descriptions-item label="前置要求">{{ formatPolicyList(policy.requirements) }}</a-descriptions-item><a-descriptions-item label="风险提示">{{ formatPolicyList(policy.warnings) }}</a-descriptions-item></a-descriptions><a-table :columns="policyColumns" :data-source="policy.items || []" :pagination="false" row-key="requirement" size="small" :scroll="{ x: 950 }"><template #bodyCell="{ column, record }"><a-tag v-if="column.key === 'allowed'" :color="record.allowed ? 'green' : 'red'">{{ record.allowed ? '允许' : '阻断' }}</a-tag><span v-else-if="column.key === 'lockedVersion'">{{ record.lockedVersion || '-' }}</span><span v-else-if="column.key === 'artifactPath'">{{ record.artifactPath || '-' }}</span><span v-else-if="column.key === 'message'">{{ formatPolicyItemMessage(record) }}</span></template></a-table></a-tab-pane>
      </a-tabs>
      <template #footer>
         <div class="dialog-footer">
            <a-button type="primary" :disabled="isCapabilityOperationBlocked(result.capability, 'dependency_install')" :loading="loading" @click="emit('dry-run')">生成安装计划</a-button>
            <a-button
               type="primary"
               :disabled="!canInstall"
               :loading="loading"
               @click="emit('install')"
               v-hasPermi="['system:plugin:edit']"
            >执行依赖安装</a-button>
            <a-button @click="emit('update:modelValue', false)">关 闭</a-button>
         </div>
      </template>
   </ResourceDrawer>
</template>

<script setup name="PluginDependencyDialog">
import { computed } from "vue";
import { CopyOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import feedback from '@/plugins/antdFeedback'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  result: {
    type: Object,
    default: () => ({})
  },
  loading: {
    type: Boolean,
    default: false
  },
  isCapabilityOperationBlocked: {
    type: Function,
    required: true
  },
  formatBoolean: {
    type: Function,
    required: true
  },
  getDependencyTagType: {
    type: Function,
    required: true
  },
  formatDependencyInstalled: {
    type: Function,
    required: true
  },
  formatDependencyVersionSatisfied: {
    type: Function,
    required: true
  }
});

const emit = defineEmits(["update:modelValue", "dry-run", "install"]);
const dependencyColumns = [{ title: '类型', dataIndex: 'kind', key: 'kind', width: 90, align: 'center' }, { title: '依赖', dataIndex: 'requirement', key: 'requirement', width: 200, ellipsis: true }, { title: '已安装版本', key: 'installedVersion', width: 130, ellipsis: true }, { title: '安装状态', key: 'installed', width: 100, align: 'center' }, { title: '版本满足', key: 'satisfied', width: 100, align: 'center' }, { title: '说明', dataIndex: 'message', key: 'message', width: 240, ellipsis: true }];
const planColumns = [{ title: '类型', dataIndex: 'kind', key: 'kind', width: 90 }, { title: '依赖', dataIndex: 'requirement', key: 'requirement', width: 150, ellipsis: true }, { title: '工作目录', dataIndex: 'workdir', key: 'workdir', width: 220, ellipsis: true }, { title: '命令', key: 'command', width: 300 }];
const policyColumns = [{ title: '类型', dataIndex: 'kind', key: 'kind', width: 90 }, { title: '依赖', dataIndex: 'requirement', key: 'requirement', width: 180, ellipsis: true }, { title: '判定', key: 'allowed', width: 90 }, { title: '锁定版本', key: 'lockedVersion', width: 120, ellipsis: true }, { title: '离线制品', key: 'artifactPath', width: 220, ellipsis: true }, { title: '说明', key: 'message', width: 260, ellipsis: true }];
const tagColor = type => ({ success: 'green', warning: 'orange', danger: 'red', info: 'default' }[type] || 'default');

const dependencyProblemCount = computed(() => {
  const dependencies = Array.isArray(props.result.dependencies) ? props.result.dependencies : [];
  return dependencies.filter(item => item.status !== "skipped" && !item.ok).length;
});

const policy = computed(() => {
  const policyPayload = props.result.policy;
  return policyPayload && typeof policyPayload === "object" ? policyPayload : null;
});

const policyAllowed = computed(() => policy.value?.allowed === true);

const policyTagType = computed(() => policyAllowed.value ? "success" : "warning");

const policySummaryTitle = computed(() => policyAllowed.value ? "策略允许执行真实安装" : "策略已阻断真实安装");

const policyMessages = computed(() => {
  if (!policy.value) {
    return [];
  }
  return [
    ...(Array.isArray(policy.value.reasons) ? policy.value.reasons : []),
    ...(Array.isArray(policy.value.requirements) ? policy.value.requirements : []),
    ...(Array.isArray(policy.value.warnings) ? policy.value.warnings : [])
  ];
});

const policyNextStep = computed(() => {
  if (!policy.value || policyAllowed.value) {
    return "";
  }
  if (policy.value.mode === "plan_only") {
    return "Web 管理端仅生成安装计划；真实安装请使用 CLI install-deps 受控执行。";
  }
  return "请按策略要求补齐确认参数、锁文件或离线制品后重试。";
});

const canInstall = computed(() => {
  return !props.isCapabilityOperationBlocked(props.result.capability, "dependency_install")
    && Array.isArray(props.result.plan)
    && props.result.plan.length > 0
    && policy.value?.allowed !== false;
});

function formatPolicyList(items) {
  return Array.isArray(items) && items.length ? items.join("；") : "-";
}

function formatPolicyItemMessage(item) {
  return formatPolicyList([
    ...(Array.isArray(item.reasons) ? item.reasons : []),
    ...(Array.isArray(item.requirements) ? item.requirements : []),
    ...(Array.isArray(item.warnings) ? item.warnings : [])
  ]);
}

function copyDependencyCommand(commandText) {
  if (!commandText) {
    return;
  }
  copyText(commandText).then(() => {
    feedback.msgSuccess("命令已复制");
  }).catch(() => {
    feedback.msgError("复制失败，请手动复制命令");
  });
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!ok) {
    throw new Error("copy failed");
  }
}
</script>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}

.dependency-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.dependency-summary-item {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--workspace-border);
  border-radius: 4px;
  background: var(--workspace-surface-muted);
}

.dependency-summary-label {
  display: block;
  margin-bottom: 6px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
}

.dependency-summary-value {
  color: var(--workspace-text);
  font-size: 14px;
  font-weight: 600;
  word-break: break-all;
}

.dependency-summary-value.is-danger {
  color: #f56c6c;
}

.dependency-policy-messages {
  display: grid;
  gap: 4px;
}

.dependency-policy-next {
  margin-top: 6px;
  color: var(--workspace-text-secondary);
}

.dependency-command-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dependency-command-text {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--workspace-text);
  font-family: monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1200px) {
  .dependency-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .dependency-summary {
    grid-template-columns: 1fr;
  }
}
</style>
