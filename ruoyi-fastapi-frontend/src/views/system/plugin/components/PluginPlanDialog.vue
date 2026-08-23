<template>
   <ResourceDrawer :title="title" :open="modelValue" :width="920" destroy-on-close @close="emit('update:modelValue', false)">
      <a-alert
         v-if="planResult.message"
         :message="planResult.message"
         :type="planResult.ok ? 'success' : 'warning'"
         show-icon
         class="mb16"
      />
      <div class="plan-summary">
         <div class="plan-summary-item">
            <span class="plan-summary-label">计划操作</span>
            <span class="plan-summary-value">{{ formatPluginOperation(planResult.operation) }}</span>
         </div>
         <div class="plan-summary-item">
            <span class="plan-summary-label">目标插件</span>
            <span class="plan-summary-value">{{ planResult.requestedPluginIds.length }}</span>
         </div>
         <div class="plan-summary-item">
            <span class="plan-summary-label">可执行</span>
            <span class="plan-summary-value">{{ planResult.executablePluginIds.length }}</span>
         </div>
         <div class="plan-summary-item">
            <span class="plan-summary-label">阻塞项</span>
            <span class="plan-summary-value is-danger">{{ planResult.blockerCount }}</span>
         </div>
      </div>
      <a-descriptions :column="1" bordered class="mb16">
         <a-descriptions-item label="目标范围">{{ formatPluginIdsForDisplay(planResult.requestedPluginIds) }}</a-descriptions-item>
         <a-descriptions-item label="执行顺序">
            <a-tag
               v-for="pluginId in planResult.executablePluginIds"
               :key="pluginId"
               class="plan-order-tag"
            >{{ pluginId }}</a-tag>
            <span v-if="!planResult.executablePluginIds.length">-</span>
         </a-descriptions-item>
      </a-descriptions>

      <a-tabs>
         <a-tab-pane key="items" tab="计划项"><a-table :columns="itemColumns" :data-source="planResult.items" :pagination="false" row-key="pluginId" size="small" :scroll="{ x: 1000 }"><template #bodyCell="{ column, record }"><a-tag v-if="column.key === 'requested'" :color="record.requested ? 'green' : 'default'">{{ record.requested ? '是' : '依赖' }}</a-tag><a-tag v-else-if="column.key === 'ready'" :color="tagColor(getPlanReadyTagType(record.ready))">{{ record.ready ? '是' : '否' }}</a-tag><span v-else-if="column.key === 'dependencies'">{{ formatPlanDependencies(record.dependencies) }}</span><a-tag v-else-if="column.key === 'blockers'" :color="record.blockers?.length ? 'red' : 'green'">{{ record.blockers?.length || 0 }}</a-tag></template></a-table></a-tab-pane>
         <a-tab-pane key="blockers" tab="阻塞原因"><a-table :columns="blockerColumns" :data-source="planResult.blockers" :pagination="false" row-key="message" size="small" :scroll="{ x: 760 }"><template #bodyCell="{ column, record }"><a-tag v-if="column.key === 'status'" color="red">{{ getPlanBlockerStatusLabel(record.status) }}</a-tag></template></a-table></a-tab-pane>
         <a-tab-pane key="result" tab="执行结果"><a-descriptions :column="4" bordered class="mb16"><a-descriptions-item label="总数">{{ batchResult.summary.total }}</a-descriptions-item><a-descriptions-item label="成功">{{ batchResult.summary.succeeded }}</a-descriptions-item><a-descriptions-item label="失败">{{ batchResult.summary.failed }}</a-descriptions-item><a-descriptions-item label="跳过">{{ batchResult.summary.skipped }}</a-descriptions-item></a-descriptions><a-table :columns="resultColumns" :data-source="batchResult.executed" :pagination="false" row-key="pluginId" size="small" :scroll="{ x: 980 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'operation'">{{ formatPluginOperation(record.operation) }}</span><a-tag v-else-if="column.key === 'status'" :color="record.ok ? 'green' : 'red'">{{ record.status || '-' }}</a-tag><span v-else-if="column.key === 'durationMs'">{{ record.durationMs ?? '-' }}ms</span></template></a-table></a-tab-pane>
      </a-tabs>

      <template #footer>
         <div class="dialog-footer">
            <a-switch
               :checked="continueOnError"
               checked-children="失败后继续"
               un-checked-children="失败即中止"
               class="mr12"
               @update:checked="emit('update:continueOnError', $event)"
            />
            <a-button
               type="primary"
               :disabled="!canExecuteBatchPlan"
               :loading="loading"
               @click="emit('execute')"
               v-hasPermi="['system:plugin:edit']"
            >执行计划：{{ formatPluginOperation(planResult.operation) }}</a-button>
            <a-button @click="emit('update:modelValue', false)">关 闭</a-button>
         </div>
      </template>
   </ResourceDrawer>
</template>

<script setup name="PluginPlanDialog">
import { computed } from "vue";
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: "插件依赖计划"
  },
  planResult: {
    type: Object,
    required: true
  },
  batchResult: {
    type: Object,
    required: true
  },
  continueOnError: {
    type: Boolean,
    default: false
  },
  loading: {
    type: Boolean,
    default: false
  },
  formatPluginOperation: {
    type: Function,
    required: true
  },
  formatPluginIdsForDisplay: {
    type: Function,
    required: true
  },
  formatPlanDependencies: {
    type: Function,
    required: true
  },
  getPlanReadyTagType: {
    type: Function,
    required: true
  },
  getPlanBlockerStatusLabel: {
    type: Function,
    required: true
  }
});

const emit = defineEmits(["update:modelValue", "update:continueOnError", "execute"]);
const itemColumns = [
  { title: '顺序', dataIndex: 'order', key: 'order', width: 70, align: 'center' }, { title: '插件ID', dataIndex: 'pluginId', key: 'pluginId', width: 140, ellipsis: true }, { title: '插件名称', dataIndex: 'name', key: 'name', width: 150, ellipsis: true }, { title: '版本', dataIndex: 'version', key: 'version', width: 100, align: 'center' }, { title: '显式选择', key: 'requested', width: 90, align: 'center' }, { title: '可执行', key: 'ready', width: 90, align: 'center' }, { title: '依赖', key: 'dependencies', width: 180, ellipsis: true }, { title: '阻塞项', key: 'blockers', width: 90, align: 'center' },
];
const blockerColumns = [{ title: '插件ID', dataIndex: 'pluginId', key: 'pluginId', width: 140, ellipsis: true }, { title: '依赖插件', dataIndex: 'dependencyId', key: 'dependencyId', width: 140, ellipsis: true }, { title: '状态', key: 'status', width: 150, align: 'center' }, { title: '说明', dataIndex: 'message', key: 'message', width: 320, ellipsis: true }];
const resultColumns = [{ title: '插件ID', dataIndex: 'pluginId', key: 'pluginId', width: 140, ellipsis: true }, { title: '操作', key: 'operation', width: 90, align: 'center' }, { title: '状态', key: 'status', width: 90, align: 'center' }, { title: '耗时', key: 'durationMs', width: 90, align: 'center' }, { title: '说明', dataIndex: 'message', key: 'message', width: 220, ellipsis: true }, { title: '建议', dataIndex: 'suggestion', key: 'suggestion', width: 260, ellipsis: true }];
const tagColor = type => ({ success: 'green', warning: 'orange', danger: 'red', info: 'default' }[type] || 'default');

const canExecuteBatchPlan = computed(() => {
  return props.planResult.ok && props.planResult.executablePluginIds.length > 0;
});
</script>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}

.mr12 {
  margin-right: 12px;
}

.plan-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.plan-summary-item {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--workspace-border);
  border-radius: 4px;
  background: var(--workspace-surface-muted);
}

.plan-summary-label {
  display: block;
  margin-bottom: 6px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
}

.plan-summary-value {
  color: var(--workspace-text);
  font-size: 14px;
  font-weight: 600;
  word-break: break-all;
}

.plan-summary-value.is-danger {
  color: #f56c6c;
}

.plan-order-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}

@media (max-width: 1200px) {
  .plan-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .plan-summary {
    grid-template-columns: 1fr;
  }
}
</style>
