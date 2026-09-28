<template>
  <ResourceDrawer title="插件详情" :open="modelValue" :width="920" destroy-on-close @close="emit('update:modelValue', false)">
    <a-tabs>
      <a-tab-pane key="overview" tab="概览">
        <div class="detail-section-title detail-section-title-first">基础信息</div>
        <a-descriptions :column="2" bordered class="mb16"><a-descriptions-item label="插件ID">{{ detail.pluginId }}</a-descriptions-item><a-descriptions-item label="插件名称">{{ detail.pluginName }}</a-descriptions-item><a-descriptions-item label="源码版本">{{ detail.version }}</a-descriptions-item><a-descriptions-item label="已安装版本">{{ detail.installedVersion || '-' }}</a-descriptions-item><a-descriptions-item label="启用设置">{{ detail.enabled === '0' ? '启用' : '停用' }}</a-descriptions-item><a-descriptions-item label="插件状态">{{ getStatusLabel(detail.status) }}</a-descriptions-item><a-descriptions-item label="来源">{{ detail.source || '-' }}</a-descriptions-item><a-descriptions-item label="更新时间">{{ formatPluginTime(detail.updateTime) }}</a-descriptions-item><a-descriptions-item label="分类">{{ detail.metadata?.category || '-' }}</a-descriptions-item><a-descriptions-item label="标签">{{ formatDetailTags(detail.metadata?.tags) }}</a-descriptions-item><a-descriptions-item label="作者">{{ detail.metadata?.author || '-' }}</a-descriptions-item><a-descriptions-item label="许可证">{{ detail.metadata?.license || '-' }}</a-descriptions-item><a-descriptions-item label="最近错误" :span="2">{{ detail.lastError || '-' }}</a-descriptions-item><a-descriptions-item label="插件说明" :span="2">{{ detail.description || '-' }}</a-descriptions-item></a-descriptions>
        <div class="detail-section-title">后端声明</div><a-descriptions :column="2" bordered class="mb16"><a-descriptions-item label="后端路径" :span="2">{{ detail.backendPath || '-' }}</a-descriptions-item><a-descriptions-item label="后端模块" :span="2">{{ detail.backend?.module || '-' }}</a-descriptions-item><a-descriptions-item label="自动扫描路由">{{ formatYesNo(detail.backend?.autoScanRouters) }}</a-descriptions-item><a-descriptions-item label="定时任务">{{ detail.backend?.jobs?.length || 0 }}</a-descriptions-item></a-descriptions>
        <div class="detail-section-title">前端声明</div><a-descriptions :column="2" bordered><a-descriptions-item label="前端路径" :span="2">{{ detail.frontendPath || '-' }}</a-descriptions-item><a-descriptions-item label="前端插件ID">{{ detail.frontend?.pluginId || '-' }}</a-descriptions-item><a-descriptions-item label="前端基础路径">{{ detail.frontend?.basePath || '-' }}</a-descriptions-item><a-descriptions-item label="视图目录">{{ detail.frontend?.viewsPath || '-' }}</a-descriptions-item><a-descriptions-item label="API 目录">{{ detail.frontend?.apiPath || '-' }}</a-descriptions-item><a-descriptions-item label="前端交付">{{ getFrontendDeliveryLabel(detail.frontend?.delivery) }}</a-descriptions-item><a-descriptions-item label="菜单声明">{{ detail.frontend?.menus?.length || 0 }}</a-descriptions-item></a-descriptions>
      </a-tab-pane>
      <a-tab-pane key="menus" tab="菜单"><a-table :columns="menuColumns" :data-source="detail.frontend?.menus || []" :pagination="false" row-key="path" size="small" :scroll="{ x: 860 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'perms'">{{ record.perms || '-' }}</span></template></a-table></a-tab-pane>
      <a-tab-pane key="permissions" tab="权限"><a-table :columns="permissionColumns" :data-source="detail.permissions || []" :pagination="false" row-key="code" size="small"><template #bodyCell="{ column, record }"><span v-if="column.key === 'name'">{{ record.name || '-' }}</span><span v-else-if="column.key === 'description'">{{ record.description || '-' }}</span></template></a-table></a-tab-pane>
      <a-tab-pane key="config" tab="配置"><a-table :columns="configColumns" :data-source="detail.config || []" :pagination="false" row-key="key" size="small" :scroll="{ x: 1100 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'label'">{{ record.label || '-' }}</span><a-tag v-else-if="column.key === 'required'" :color="record.required ? 'orange' : 'default'">{{ record.required ? '是' : '否' }}</a-tag><a-tag v-else-if="column.key === 'secret'" :color="record.secret ? 'orange' : 'default'">{{ record.secret ? '是' : '否' }}</a-tag><span v-else-if="column.key === 'default'">{{ formatConfigDefaultValue(record) }}</span><span v-else-if="column.key === 'constraint'">{{ formatConfigConstraint(record) }}</span></template></a-table></a-tab-pane>
      <a-tab-pane key="dependencies" tab="依赖">
        <div class="detail-section-title detail-section-title-first">运行依赖</div><a-table :columns="groupColumns" :data-source="detailDependencyRows" :pagination="false" row-key="kind" size="small"><template #bodyCell="{ column, record }"><span v-if="column.key === 'count'">{{ record.items.length }}</span><div v-else-if="column.key === 'items'" class="detail-tag-list"><a-tag v-for="item in record.items" :key="record.kind + item" :color="tagColor(record.tagType)">{{ item }}</a-tag><span v-if="!record.items.length" class="detail-empty-text">-</span></div></template></a-table>
        <div class="detail-section-title">数据库脚本</div><a-table :columns="groupColumns" :data-source="detailScriptRows" :pagination="false" row-key="kind" size="small"><template #bodyCell="{ column, record }"><span v-if="column.key === 'count'">{{ record.items.length }}</span><div v-else-if="column.key === 'items'" class="detail-tag-list"><a-tag v-for="item in record.items" :key="record.kind + item">{{ item }}</a-tag><span v-if="!record.items.length" class="detail-empty-text">-</span></div></template></a-table>
        <div class="detail-section-title">执行历史</div><a-table :columns="migrationColumns" :data-source="migrationHistory" :loading="migrationLoading" :pagination="false" :row-key="record => `${record.migrationPath}-${record.version}`" size="small" :scroll="{ x: 1250 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'version'">{{ record.version || '-' }}</span><a-tag v-else-if="column.key === 'status'" :color="tagColor(getMigrationStatusTagType(record.status))">{{ getMigrationStatusLabel(record.status) }}</a-tag><span v-else-if="column.key === 'attemptCount'">{{ record.attemptCount ?? 0 }}</span><span v-else-if="column.key === 'statementCount'">{{ record.statementCount ?? 0 }}</span><span v-else-if="column.key === 'startedTime'">{{ formatPluginTime(record.startedTime || record.createTime) }}</span><span v-else-if="column.key === 'finishedTime'">{{ formatPluginTime(record.finishedTime) }}</span><span v-else-if="column.key === 'errorMessage'">{{ record.errorMessage || '-' }}</span><div v-else-if="column.key === 'actions'" class="detail-row-actions"><a-tooltip v-if="canMarkMigrationSuccess(record)" title="标记成功"><a-button type="link" @click="emit('mark-migration-success', record)" v-hasPermi="['system:plugin:edit']"><CheckCircleOutlined /></a-button></a-tooltip><a-tooltip v-if="canMarkMigrationFailed(record)" title="标记失败"><a-button type="link" danger @click="emit('mark-migration-failed', record)" v-hasPermi="['system:plugin:edit']"><CloseCircleOutlined /></a-button></a-tooltip><span v-if="!canMarkMigrationSuccess(record) && !canMarkMigrationFailed(record)">-</span></div></template></a-table>
        <div class="detail-section-title">插件依赖</div><a-table :columns="pluginDependencyColumns" :data-source="detail.pluginDependencies || []" :pagination="false" row-key="id" size="small"><template #bodyCell="{ column, record }"><span v-if="column.key === 'version'">{{ record.version || '-' }}</span></template></a-table>
        <div class="detail-section-title">定时任务</div><a-table :columns="jobColumns" :data-source="detail.backend?.jobs || []" :pagination="false" row-key="id" size="small" :scroll="{ x: 950 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'name'">{{ record.name || '-' }}</span><a-tag v-else-if="column.key === 'enabled'" :color="record.enabled ? 'green' : 'default'">{{ record.enabled ? '是' : '否' }}</a-tag></template></a-table>
      </a-tab-pane>
    </a-tabs>
    <template #footer><a-button v-if="detail.status === 'error'" type="primary" @click="emit('repair')" v-hasPermi="['system:plugin:edit']">重新安装修复</a-button><a-button @click="emit('update:modelValue', false)">关 闭</a-button></template>
  </ResourceDrawer>
</template>

<script setup name="PluginDetailDialog">
import { computed } from "vue";
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  detail: {
    type: Object,
    default: () => ({})
  },
  getStatusLabel: {
    type: Function,
    required: true
  },
  formatPluginTime: {
    type: Function,
    required: true
  },
  formatConfigDefaultValue: {
    type: Function,
    required: true
  },
  formatConfigConstraint: {
    type: Function,
    required: true
  },
  migrationHistory: {
    type: Array,
    default: () => []
  },
  migrationLoading: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(["update:modelValue", "repair", "mark-migration-success", "mark-migration-failed"]);
const menuColumns = [{ title: '名称', dataIndex: 'name', key: 'name', width: 140, ellipsis: true }, { title: '路径', dataIndex: 'path', key: 'path', width: 140, ellipsis: true }, { title: '组件', dataIndex: 'component', key: 'component', width: 180, ellipsis: true }, { title: '权限', key: 'perms', width: 160, ellipsis: true }, { title: '类型', dataIndex: 'type', key: 'type', width: 70 }, { title: '排序', dataIndex: 'orderNum', key: 'orderNum', width: 70 }];
const permissionColumns = [{ title: '权限标识', dataIndex: 'code', key: 'code', width: 180 }, { title: '展示名称', key: 'name', width: 140 }, { title: '说明', key: 'description', width: 220 }];
const configColumns = [{ title: '配置键', dataIndex: 'key', key: 'key', width: 160 }, { title: '名称', key: 'label', width: 140 }, { title: '类型', dataIndex: 'type', key: 'type', width: 100 }, { title: '必填', key: 'required', width: 70 }, { title: '敏感', key: 'secret', width: 70 }, { title: '默认值', key: 'default', width: 160 }, { title: '约束', key: 'constraint', width: 200 }, { title: '说明', dataIndex: 'description', key: 'description', width: 220 }];
const groupColumns = [{ title: '类型', dataIndex: 'label', key: 'label', width: 120 }, { title: '数量', key: 'count', width: 80 }, { title: '依赖项', key: 'items', width: 520 }];
const migrationColumns = [{ title: '脚本', dataIndex: 'migrationPath', key: 'migrationPath', width: 220, ellipsis: true }, { title: '版本', key: 'version', width: 100 }, { title: '状态', key: 'status', width: 120 }, { title: '次数', key: 'attemptCount', width: 70 }, { title: '语句', key: 'statementCount', width: 70 }, { title: '开始时间', key: 'startedTime', width: 160 }, { title: '结束时间', key: 'finishedTime', width: 160 }, { title: '错误信息', key: 'errorMessage', width: 220, ellipsis: true }, { title: '操作', key: 'actions', width: 110, fixed: 'right' }];
const pluginDependencyColumns = [{ title: '插件ID', dataIndex: 'id', key: 'id', width: 160 }, { title: '版本约束', key: 'version', width: 140 }, { title: '说明', dataIndex: 'description', key: 'description', width: 220 }];
const jobColumns = [{ title: '任务ID', dataIndex: 'id', key: 'id', width: 140 }, { title: '名称', key: 'name', width: 140 }, { title: '调用目标', dataIndex: 'callable', key: 'callable', width: 220 }, { title: 'Cron', dataIndex: 'cronExpression', key: 'cronExpression', width: 150 }, { title: '默认启用', key: 'enabled', width: 90 }, { title: '说明', dataIndex: 'description', key: 'description', width: 220 }];
const tagColor = type => ({ success: 'green', warning: 'orange', danger: 'red', primary: 'blue', info: 'default' }[type] || 'default');

const detailDependencyRows = computed(() => {
  const dependencies = props.detail.dependencies || {};
  return [
    {
      kind: "python",
      label: "Python",
      tagType: "success",
      items: normalizeDetailItems(dependencies.python)
    },
    {
      kind: "npm",
      label: "npm",
      tagType: "primary",
      items: normalizeDetailItems(dependencies.npm)
    },
    {
      kind: "npmDev",
      label: "npmDev",
      tagType: "warning",
      items: normalizeDetailItems(dependencies.npmDev)
    }
  ];
});

const detailScriptRows = computed(() => {
  const backend = props.detail.backend || {};
  return [
    {
      kind: "migration",
      label: "Migration",
      items: normalizeDetailItems(backend.migrations)
    },
    {
      kind: "seed",
      label: "Seed",
      items: normalizeDetailItems(backend.seeds)
    }
  ];
});

function formatYesNo(value) {
  if (value === undefined || value === null) {
    return "-";
  }
  return value ? "是" : "否";
}

function formatDetailTags(tags) {
  return Array.isArray(tags) && tags.length ? tags.join(", ") : "-";
}

function getFrontendDeliveryLabel(delivery) {
  if (!delivery) {
    return "-";
  }
  const typeMap = {
    none: "无前端资源",
    source: "源码交付"
  };
  return typeMap[delivery.type] || delivery.type || "-";
}

function normalizeDetailItems(items) {
  return Array.isArray(items) ? items.filter(Boolean) : [];
}

function getMigrationStatusLabel(status) {
  const statusMap = {
    running: "执行中/中断",
    success: "成功",
    failed: "失败",
    unknown: "未知"
  };
  return statusMap[status] || status || "-";
}

function getMigrationStatusTagType(status) {
  const statusMap = {
    running: "warning",
    success: "success",
    failed: "danger",
    unknown: "info"
  };
  return statusMap[status] || "info";
}

function canMarkMigrationSuccess(row) {
  return ["running", "failed"].includes(row?.status);
}

function canMarkMigrationFailed(row) {
  return row?.status === "running";
}
</script>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}

.detail-section-title {
  margin: 14px 0 8px;
  color: var(--workspace-text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.detail-section-title-first {
  margin-top: 0;
}

.detail-tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 24px;
  align-items: center;
}

.detail-empty-text {
  color: var(--workspace-text-muted);
}

.detail-row-actions {
  display: flex;
  justify-content: center;
  gap: 4px;
}
</style>
