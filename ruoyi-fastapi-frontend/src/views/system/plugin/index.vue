<template>
   <WorkspacePage title="插件管理" subtitle="插件生命周期、依赖、配置与诊断">
      <a-form :model="queryParams" ref="queryRef" layout="inline" v-show="showSearch" label-width="68px" class="plugin-query-form">
         <a-form-item label="插件ID" name="pluginId">
            <a-input
               v-model:value="queryParams.pluginId"
               placeholder="请输入插件ID"
               allow-clear
               @keyup.enter="handleQuery"
            />
         </a-form-item>
         <a-form-item label="插件名称" name="pluginName">
            <a-input
               v-model:value="queryParams.pluginName"
               placeholder="请输入插件名称"
               allow-clear
               @keyup.enter="handleQuery"
            />
         </a-form-item>
         <a-form-item label="启用设置" name="enabled">
            <a-select v-model:value="queryParams.enabled" placeholder="启用设置" allow-clear>
               <a-select-option label="启用" value="0" />
               <a-select-option label="停用" value="1" />
            </a-select>
         </a-form-item>
         <a-form-item label="插件状态" name="status">
            <a-select v-model:value="queryParams.status" placeholder="插件状态" allow-clear>
               <a-select-option v-for="item in pluginStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </a-select>
         </a-form-item>
         <a-form-item>
            <a-button type="primary" @click="handleQuery">搜索</a-button>
            <a-button @click="resetQuery">重置</a-button>
         </a-form-item>
      </a-form>

      <a-row :gutter="10" class="mb8">
         <a-col :span="1.5">
            <a-button type="primary" :disabled="!selectedPluginIds.length" @click="handlePlan('install')" v-hasPermi="['system:plugin:query']">安装计划</a-button>
         </a-col>
         <a-col :span="1.5">
            <a-button type="primary" :disabled="!selectedPluginIds.length" @click="handlePlan('enable')" v-hasPermi="['system:plugin:query']">启用计划</a-button>
         </a-col>
         <a-col :span="1.5">
            <a-button :disabled="!selectedPluginIds.length" @click="handlePlan('upgrade')" v-hasPermi="['system:plugin:query']">升级计划</a-button>
         </a-col>
         <a-col :span="1.5">
            <a-button @click="handleOperationLog" v-hasPermi="['system:plugin:query']">审计记录</a-button>
         </a-col>
         <a-col :span="1.5">
            <a-tooltip :title="formatPluginIdsForDisplay(selectedPluginIds)" :disabled="!selectedPluginIds.length" placement="top">
               <a-tag
                  :color="selectedPluginIds.length ? 'green' : 'default'"
                  :closable="!!selectedPluginIds.length"
                  :disable-transitions="true"
                  class="plugin-selection-tag"
                  @close="clearSelectedPlugins"
               >{{ planTargetSummary }}</a-tag>
            </a-tooltip>
         </a-col>
         <a-col :span="1.5"><a-button @click="showSearch = !showSearch">筛选</a-button></a-col>
         <a-col :span="1.5"><a-button @click="getList">刷新</a-button></a-col>
      </a-row>

      <ResourceTable :columns="pluginColumns" :rows="pluginList" :loading="loading" :pagination="false" row-key="pluginId" :scroll="{ x: 1350 }">
         <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allPluginsSelected" :indeterminate="somePluginsSelected" @change="toggleAllPlugins" /></template>
         <template #bodyCell="{ column, record }">
            <a-checkbox v-if="column.key === 'selection'" :checked="selectedPluginIds.includes(record.pluginId)" :disabled="!canSelectForBatch(record)" @change="event => togglePluginSelection(record, event.target.checked)" />
            <span v-else-if="column.key === 'installedVersion'">{{ record.installedVersion || '-' }}</span>
            <a-tooltip v-else-if="column.key === 'enabled'" :title="getEnabledSwitchTooltip(record)"><span class="plugin-switch-tooltip-target"><a-switch v-model:checked="record.enabled" checked-value="0" un-checked-value="1" :disabled="isEnabledSwitchBlocked(record)" @change="handleEnabledChange(record)" v-hasPermi="['system:plugin:edit']" /></span></a-tooltip>
            <a-tag v-else-if="column.key === 'status'" :color="tagColor(getStatusTagType(record.status))">{{ getStatusLabel(record.status) }}</a-tag>
            <span v-else-if="column.key === 'updateTime'">{{ formatPluginTime(record.updateTime) }}</span>
            <a-space v-else-if="column.key === 'actions'" wrap><a-button type="link" @click="handleDetail(record)" v-hasPermi="['system:plugin:query']">详情</a-button><a-button v-if="!isOrphanPlugin(record)" type="link" @click="handleConfig(record)" v-hasPermi="['system:plugin:query']">配置</a-button><a-button v-if="!isOrphanPlugin(record)" type="link" @click="handleDependencies(record)" v-hasPermi="['system:plugin:query']">依赖</a-button><a-button v-if="!isOrphanPlugin(record)" type="link" @click="handleCheck(record)" v-hasPermi="['system:plugin:query']">检查</a-button><a-button v-if="!isOrphanPlugin(record)" type="link" @click="handleHealth(record)" v-hasPermi="['system:plugin:query']">健康</a-button><a-button v-if="!isOrphanPlugin(record)" type="link" @click="handleDiagnose(record)" v-hasPermi="['system:plugin:query']">诊断</a-button><a-button v-if="canInstall(record)" type="link" :disabled="isOperationBlocked(record, 'install')" @click="handleInstallDryRun(record)" v-hasPermi="['system:plugin:edit']">安装</a-button><a-button v-if="canUpgrade(record)" type="link" :disabled="isOperationBlocked(record, 'upgrade')" @click="handleUpgradeDryRun(record)" v-hasPermi="['system:plugin:edit']">升级</a-button><a-button v-if="canUninstall(record)" type="link" danger :disabled="isOperationBlocked(record, 'uninstall')" @click="handleUninstallDryRun(record)" v-hasPermi="['system:plugin:edit']">卸载</a-button><a-button v-if="isOrphanPlugin(record)" type="link" danger @click="handlePurgeDryRun(record)" v-hasPermi="['system:plugin:remove']">清理</a-button></a-space>
         </template>
      </ResourceTable>

      <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" :page-size-options="['10', '20', '30', '50']" show-size-changer show-quick-jumper responsive @change="getList" />

      <plugin-detail-dialog
         v-model="detailOpen"
         :detail="detail"
         :get-status-label="getStatusLabel"
         :format-plugin-time="formatPluginTime"
         :format-config-default-value="formatConfigDefaultValue"
         :format-config-constraint="formatConfigConstraint"
         :migration-history="migrationHistory"
         :migration-loading="migrationLoading"
         @repair="handleRepairFromDetail"
         @mark-migration-success="handleMarkMigrationSuccess"
         @mark-migration-failed="handleMarkMigrationFailed"
      />

      <plugin-dependency-dialog
         v-model="dependencyOpen"
         :result="dependencyResult"
         :loading="dependencyLoading"
         :is-capability-operation-blocked="isCapabilityOperationBlocked"
         :format-boolean="formatBoolean"
         :get-dependency-tag-type="getDependencyTagType"
         :format-dependency-installed="formatDependencyInstalled"
         :format-dependency-version-satisfied="formatDependencyVersionSatisfied"
         @dry-run="handleDependencyDryRun"
         @install="handleDependencyInstall"
      />

      <plugin-diagnostic-dialog
         v-model="diagnosticOpen"
         :title="diagnosticTitle"
         :result="diagnosticResult"
         :loading="diagnosticLoading"
         :format-json="formatJson"
         :format-boolean="formatBoolean"
         :get-validation-level-label="getValidationLevelLabel"
         :get-validation-level-tag-type="getValidationLevelTagType"
      />

      <plugin-plan-dialog
         v-model="planOpen"
         v-model:continue-on-error="batchContinueOnError"
         :title="planTitle"
         :plan-result="planResult"
         :batch-result="batchResult"
         :loading="planLoading"
         :format-plugin-operation="formatPluginOperation"
         :format-plugin-ids-for-display="formatPluginIdsForDisplay"
         :format-plan-dependencies="formatPlanDependencies"
         :get-plan-ready-tag-type="getPlanReadyTagType"
         :get-plan-blocker-status-label="getPlanBlockerStatusLabel"
         @execute="handleExecuteBatch(false)"
      />

      <ResourceDrawer title="插件操作审计" :open="operationLogOpen" width="50%" @close="operationLogOpen = false">
         <div class="plugin-audit-drawer">
            <div class="plugin-audit-search" v-show="operationLogShowSearch">
               <a-form :model="operationLogQueryParams" layout="inline" label-width="68px" class="plugin-query-form plugin-log-toolbar">
                  <a-form-item label="插件ID">
                     <a-input
                        v-model:value="operationLogQueryParams.pluginId"
                        placeholder="请输入插件ID"
                        allow-clear
                        @keyup.enter="handleOperationLogQuery"
                     />
                  </a-form-item>
                  <a-form-item label="操作">
                     <a-select v-model:value="operationLogQueryParams.operation" placeholder="操作类型" allow-clear>
                        <a-select-option v-for="item in plugin_operation_type" :key="item.value" :label="item.label" :value="item.value" />
                     </a-select>
                  </a-form-item>
                  <a-form-item label="状态">
                     <a-select v-model:value="operationLogQueryParams.status" placeholder="执行状态" allow-clear>
                        <a-select-option v-for="item in operationLogStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
                     </a-select>
                  </a-form-item>
                  <a-form-item label="创建时间" class="plugin-audit-date-range">
                     <a-range-picker
                        v-model:value="operationLogDateRange"
                        value-format="YYYY-MM-DD"
                        separator="-"
                        start-placeholder="开始日期"
                        end-placeholder="结束日期"
                     />
                  </a-form-item>
                  <a-form-item>
                     <a-button type="primary" @click="handleOperationLogQuery">搜索</a-button>
                     <a-button @click="resetOperationLogQuery">重置</a-button>
                  </a-form-item>
               </a-form>
            </div>

            <a-collapse v-model:active-key="operationLogMaintenanceActive" class="plugin-audit-maintenance">
               <a-collapse-panel key="retention">
                  <template #header>
                     <span class="plugin-audit-maintenance-title">审计保留策略</span>
                     <a-tag size="small" >默认 {{ operationLogRetentionDefaultDays }} 天</a-tag>
                  </template>
                  <a-form :model="operationLogRetentionForm" layout="inline" label-width="84px" class="plugin-log-toolbar">
                     <a-form-item label="保留天数">
                        <a-input-number v-model:value="operationLogRetentionForm.retentionDays" :min="0" :max="3650" style="width: 150px" />
                     </a-form-item>
                     <a-form-item>
                        <a-button :loading="operationLogRetentionLoading" @click="handleOperationLogRetentionPreview">预览清理</a-button>
                        <a-button
                           danger
                           :loading="operationLogRetentionLoading"
                           @click="handleOperationLogRetentionClean"
                           v-hasPermi="['system:plugin:edit']"
                        >确认清理</a-button>
                     </a-form-item>
                  </a-form>
                  <a-alert
                     v-if="operationLogRetentionResult.matchedCount !== undefined"
                     :message="formatRetentionMessage(operationLogRetentionResult)"
                     :type="operationLogRetentionResult.deletedCount ? 'success' : 'info'"
                     show-icon
                     :closable="false"
                  />
               </a-collapse-panel>
            </a-collapse>

            <div class="plugin-audit-table">
               <div class="plugin-audit-table-toolbar">
                  <a-row :gutter="10" class="plugin-audit-table-actions">
                     <a-col :span="1.5">
                        <a-button
                           :loading="operationLogExportLoading"
                           @click="handleOperationLogExport"
                           v-hasPermi="['system:plugin:export']"
                        >导出</a-button>
                     </a-col>
                  </a-row>
                  <a-space><a-button @click="operationLogShowSearch = !operationLogShowSearch">筛选</a-button><a-button @click="getOperationLogList">刷新</a-button></a-space>
               </div>
               <ResourceTable :columns="operationLogColumns" :rows="operationLogList" :loading="operationLogLoading" :pagination="false" row-key="operationId" :scroll="{ x: 850 }"><template #bodyCell="{ column, record }"><span v-if="column.key === 'operation'">{{ formatPluginOperation(record.operation) }}</span><a-tag v-else-if="column.key === 'status'" :color="tagColor(getOperationLogStatusTagType(record.status))">{{ getOperationLogStatusLabel(record.status) }}</a-tag><span v-else-if="column.key === 'plugins'">{{ formatPlanDependencies(record.pluginIds) }}</span><span v-else-if="column.key === 'succeeded'">{{ record.summary?.succeeded ?? 0 }}</span><span v-else-if="column.key === 'failed'">{{ record.summary?.failed ?? 0 }}</span><span v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime) }}</span><a-button v-else-if="column.key === 'actions'" type="link" @click="handleOperationLogDetail(record)">详情</a-button></template></ResourceTable>
               <a-pagination v-if="operationLogTotal > 0" v-model:current="operationLogQueryParams.pageNum" v-model:page-size="operationLogQueryParams.pageSize" :total="operationLogTotal" show-size-changer @change="getOperationLogList" />
            </div>

            <div class="drawer-footer">
               <a-button @click="operationLogOpen = false">关 闭</a-button>
            </div>
         </div>
      </ResourceDrawer>

      <ResourceDrawer title="审计详情" :open="operationLogDetailOpen" :width="920" @close="operationLogDetailOpen = false">
         <a-descriptions :column="3" bordered class="mb16">
            <a-descriptions-item label="日志ID">{{ operationLogDetail.operationId || "-" }}</a-descriptions-item>
            <a-descriptions-item label="操作">{{ formatPluginOperation(operationLogDetail.operation) }}</a-descriptions-item>
            <a-descriptions-item label="状态">{{ getOperationLogStatusLabel(operationLogDetail.status) }}</a-descriptions-item>
            <a-descriptions-item label="预演">{{ operationLogDetail.dryRun ? "是" : "否" }}</a-descriptions-item>
            <a-descriptions-item label="失败后继续">{{ operationLogDetail.continueOnError ? "是" : "否" }}</a-descriptions-item>
            <a-descriptions-item label="创建时间">{{ parseTime(operationLogDetail.createTime) || "-" }}</a-descriptions-item>
            <a-descriptions-item label="插件" :span="3">{{ formatPlanDependencies(operationLogDetail.pluginIds) }}</a-descriptions-item>
            <a-descriptions-item label="备注" :span="3">{{ operationLogDetail.remark || "-" }}</a-descriptions-item>
         </a-descriptions>
         <a-tabs>
            <a-tab-pane key="summary" tab="摘要">
               <a-descriptions :column="3" bordered class="mb16">
                  <a-descriptions-item label="成功数">{{ operationLogDetail.summary?.succeeded ?? 0 }}</a-descriptions-item>
                  <a-descriptions-item label="失败数">{{ operationLogDetail.summary?.failed ?? 0 }}</a-descriptions-item>
                  <a-descriptions-item label="跳过数">{{ operationLogDetail.summary?.skipped ?? 0 }}</a-descriptions-item>
                  <a-descriptions-item label="变更配置" :span="3">
                     {{ formatPlanDependencies(operationLogDetail.summary?.changedKeys) }}
                  </a-descriptions-item>
                  <a-descriptions-item label="失败建议" :span="3">
                     {{ operationLogDetail.failedSuggestion || "暂无" }}
                  </a-descriptions-item>
                  <a-descriptions-item label="失败步骤" :span="3">
                     {{ operationLogDetail.failedStep || operationLogDetail.result?.failedStep || operationLogDetail.result?.failed?.result?.failedStep || "暂无" }}
                  </a-descriptions-item>
               </a-descriptions>
            </a-tab-pane>
            <a-tab-pane key="validation" tab="检查结果">
               <a-textarea :value="formatJson(operationLogDetail.validationItems || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="actions" tab="动作计划">
               <a-textarea :value="formatJson(operationLogDetail.actionItems || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="plan" tab="依赖计划">
               <a-textarea :value="formatJson(operationLogDetail.planItems || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="executed" tab="执行记录">
               <a-textarea :value="formatJson(operationLogDetail.result?.executed || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="config" tab="配置变更">
               <a-textarea :value="formatJson(operationLogDetail.configChanges || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="raw" tab="原始结果">
               <a-textarea :value="formatJson(operationLogDetail.result)"  :rows="14" readonly />
            </a-tab-pane>
         </a-tabs>
         <template #footer>
            <div class="dialog-footer">
               <a-button @click="operationLogDetailOpen = false">关 闭</a-button>
            </div>
         </template>
      </ResourceDrawer>

      <plugin-config-dialog
         v-model="configOpen"
         :title="configTitle"
         :items="configItems"
         :loading="configLoading"
         :format-config-default-value="formatConfigDefaultValue"
         :format-config-constraint="formatConfigConstraint"
         @submit="submitConfig"
      />

      <ResourceDrawer :title="actionTitle" :open="actionOpen" :width="860" @close="actionOpen = false">
         <a-alert
            v-if="actionResult.message"
            :message="actionResult.message"
            :type="actionResult.ok ? 'success' : 'warning'"
            show-icon
            :closable="false"
            class="mb16"
         />
         <a-descriptions :column="3" bordered class="mb16">
            <a-descriptions-item label="插件ID">{{ actionResult.pluginId || "-" }}</a-descriptions-item>
            <a-descriptions-item label="依赖检查">{{ formatBoolean(actionResult.dependencyOk) }}</a-descriptions-item>
            <a-descriptions-item label="结构检查">{{ formatBoolean(actionResult.structureOk) }}</a-descriptions-item>
            <a-descriptions-item label="菜单冲突">{{ formatBoolean(actionResult.menuConflictOk) }}</a-descriptions-item>
            <a-descriptions-item label="预演模式">{{ actionResult.dryRun ? "是" : "否" }}</a-descriptions-item>
            <a-descriptions-item label="需要升级">{{ formatBoolean(actionResult.needsUpgrade) }}</a-descriptions-item>
         </a-descriptions>

         <a-tabs>
            <a-tab-pane v-if="actionResult.error" key="error" tab="错误详情">
               <a-alert
                  :message="actionResult.error.message || '插件操作失败'"
                  type="error"
                  show-icon
                  :closable="false"
                  class="mb16"
               />
               <a-descriptions :column="2" bordered class="mb16">
                  <a-descriptions-item label="插件ID">{{ actionResult.pluginId || pendingAction.pluginId || "-" }}</a-descriptions-item>
                  <a-descriptions-item label="操作">{{ actionResult.operation ? formatPluginOperation(actionResult.operation) : (pendingAction.label || "-") }}</a-descriptions-item>
                  <a-descriptions-item label="失败步骤">{{ actionResult.failedStep || actionResult.error.failedStep || "-" }}</a-descriptions-item>
                  <a-descriptions-item v-if="actionResult.migrationRecovery" label="Migration">
                     {{ actionResult.migrationRecovery.migrationPath || "-" }}
                  </a-descriptions-item>
                  <a-descriptions-item v-if="actionResult.migrationRecovery" label="Migration状态">
                     {{ actionResult.migrationRecovery.status || "-" }}
                  </a-descriptions-item>
                  <a-descriptions-item label="建议" :span="2">{{ actionResult.error.suggestion || "请查看后端日志或重新执行检查。" }}</a-descriptions-item>
               </a-descriptions>
               <a-textarea :value="formatJson(actionResult.error.raw)"  :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="actions" tab="动作计划">
               <a-textarea :value="formatJson(actionResult.actions || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="dependencies" tab="依赖">
               <a-textarea :value="formatJson(actionResult.dependencies || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="manifest" tab="Manifest 提示">
               <a-textarea :value="formatJson(actionResult.manifestIssues || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="structure" tab="结构错误">
               <a-textarea :value="formatJson(actionResult.structureErrors || [])" :rows="10" readonly />
            </a-tab-pane>
            <a-tab-pane key="menus" tab="菜单冲突">
               <a-textarea :value="formatJson(actionResult.menuConflicts || [])" :rows="10" readonly />
            </a-tab-pane>
         </a-tabs>

         <template #footer>
            <div class="dialog-footer">
               <a-button
                  v-if="pendingAction.type && pendingAction.type !== 'purge'"
                  type="primary"
                  :loading="actionLoading"
                  @click="handleExecutePendingAction"
                  v-hasPermi="['system:plugin:edit']"
               >执行{{ pendingAction.label }}</a-button>
               <a-button
                  v-if="pendingAction.type === 'purge'"
                  danger
                  :loading="actionLoading"
                  @click="handleExecutePendingAction"
                  v-hasPermi="['system:plugin:remove']"
               >执行{{ pendingAction.label }}</a-button>
               <a-button @click="actionOpen = false">关 闭</a-button>
            </div>
         </template>
      </ResourceDrawer>
   </WorkspacePage>
</template>

<script setup name="Plugin">
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import {
  batchPlugins,
  checkPlugin,
  checkPluginDependencies,
  diagnosePlugin,
  disablePlugin,
  enablePlugin,
  getPlugin,
  getPluginConfig,
  getPluginOperationLog,
  healthPlugin,
  installPluginDependencies,
  installPlugin,
  listPlugin,
  listPluginMigrations,
  listPluginOperationLog,
  markPluginMigrationFailed,
  markPluginMigrationSuccess,
  planPlugins,
  purgePlugin,
  retainPluginOperationLog,
  uninstallPlugin,
  updatePluginConfig,
  upgradePlugin
} from "@/api/system/plugin";
import { getConfigKey } from "@/api/system/config";
import PluginConfigDialog from "./components/PluginConfigDialog.vue";
import PluginDependencyDialog from "./components/PluginDependencyDialog.vue";
import PluginDiagnosticDialog from "./components/PluginDiagnosticDialog.vue";
import PluginDetailDialog from "./components/PluginDetailDialog.vue";
import PluginPlanDialog from "./components/PluginPlanDialog.vue";
import {
  getPlanBlockerStatusLabel,
  getPlanOperationLabel,
  getPlanReadyTagType,
  getValidationLevelLabel,
  getValidationLevelTagType,
  normalizePluginActionResult,
  normalizePluginBatchResponse,
  normalizePluginOperationLogDetail,
  normalizePluginPlanResponse
} from "@/utils/pluginPlanFormatter";

const { proxy } = getCurrentInstance();
const { plugin_operation_type } = proxy.useDict("plugin_operation_type");
const parseTime = proxy.parseTime;
const INVALID_PLUGIN_TIME_VALUES = new Set(["", "-", "0", "0-0-0 0:0:0", "0000-00-00 00:00:00"]);
const PLUGIN_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/;
const OPERATION_LOG_RETENTION_CONFIG_KEY = "sys.plugin.operationLogRetentionDays";

const pluginTableRef = ref(null);
const pluginList = ref([]);
const selectedPluginIds = ref([]);
const loading = ref(true);
const showSearch = ref(true);
const total = ref(0);
const detailOpen = ref(false);
const detail = ref({});
const migrationLoading = ref(false);
const migrationHistory = ref([]);
const actionOpen = ref(false);
const actionLoading = ref(false);
const actionTitle = ref("插件操作结果");
const actionResult = ref({});
const pendingAction = ref({});
const configOpen = ref(false);
const configLoading = ref(false);
const configTitle = ref("插件配置");
const configPluginId = ref("");
const configItems = ref([]);
let configGeneration = 0;
const dependencyOpen = ref(false);
const dependencyLoading = ref(false);
const dependencyPluginId = ref("");
const dependencyResult = ref({});
let dependencyGeneration = 0;
const diagnosticOpen = ref(false);
const diagnosticLoading = ref(false);
const diagnosticTitle = ref("插件诊断");
const diagnosticResult = ref({});
const diagnosticPluginId = ref("");
let diagnosticGeneration = 0;
const planOpen = ref(false);
const planLoading = ref(false);
const planTitle = ref("插件依赖计划");
const planResult = ref(normalizePluginPlanResponse({}));
const batchContinueOnError = ref(false);
const batchResult = ref(normalizePluginBatchResponse({}));
const operationLogOpen = ref(false);
const operationLogShowSearch = ref(true);
const operationLogLoading = ref(false);
const operationLogExportLoading = ref(false);
const operationLogList = ref([]);
const operationLogTotal = ref(0);
const operationLogDateRange = ref([]);
const operationLogDetailOpen = ref(false);
const operationLogDetail = ref({});
const operationLogRetentionLoading = ref(false);
const operationLogRetentionResult = ref({});
const operationLogRetentionDefaultDays = ref(180);
const operationLogMaintenanceActive = ref([]);
const operationLogRetentionForm = reactive({
  retentionDays: 180
});
watch(configOpen, open => {
  if (!open) {
    configGeneration += 1;
    configLoading.value = false;
  }
});
watch(dependencyOpen, open => {
  if (!open) {
    dependencyGeneration += 1;
    dependencyLoading.value = false;
  }
});
watch(diagnosticOpen, open => {
  if (!open) {
    diagnosticGeneration += 1;
    diagnosticLoading.value = false;
  }
});
const tagColor = type => ({ success: 'green', warning: 'orange', danger: 'red', primary: 'blue', info: 'default' }[type] || 'default');
const pluginColumns = [
  { key: 'selection', width: 55, align: 'center' }, { title: '插件ID', dataIndex: 'pluginId', key: 'pluginId', width: 120, ellipsis: true },
  { title: '插件名称', dataIndex: 'pluginName', key: 'pluginName', width: 140, ellipsis: true }, { title: '源码版本', dataIndex: 'version', key: 'version', width: 100 },
  { title: '已安装版本', key: 'installedVersion', width: 110 }, { title: '启用设置', key: 'enabled', width: 90 }, { title: '插件状态', key: 'status', width: 110 },
  { title: '来源', dataIndex: 'source', key: 'source', width: 80 }, { title: '更新时间', key: 'updateTime', width: 165 }, { title: '操作', key: 'actions', width: 380, fixed: 'right' },
];
const operationLogColumns = [{ title: '日志ID', dataIndex: 'operationId', key: 'operationId', width: 90 }, { title: '操作', key: 'operation', width: 90 }, { title: '状态', key: 'status', width: 90 }, { title: '插件', key: 'plugins', width: 180, ellipsis: true }, { title: '成功', key: 'succeeded', width: 70 }, { title: '失败', key: 'failed', width: 70 }, { title: '创建时间', key: 'createTime', width: 170 }, { title: '操作', key: 'actions', width: 70 }];
const selectablePlugins = computed(() => pluginList.value.filter(canSelectForBatch));
const allPluginsSelected = computed(() => selectablePlugins.value.length > 0 && selectablePlugins.value.every(row => selectedPluginIds.value.includes(row.pluginId)));
const somePluginsSelected = computed(() => selectedPluginIds.value.length > 0 && !allPluginsSelected.value);

function formatPluginTime(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string" && INVALID_PLUGIN_TIME_VALUES.has(value.trim())) {
    return "-";
  }

  const formatted = formatPluginIsoTime(value) || parseTime(value);
  if (!formatted || INVALID_PLUGIN_TIME_VALUES.has(String(formatted).trim())) {
    return "-";
  }

  return formatted;
}

function formatPluginIsoTime(value) {
  if (typeof value !== "string") {
    return "";
  }
  const matched = value.trim().match(PLUGIN_TIME_PATTERN);
  if (!matched) {
    return "";
  }

  return `${matched[1]}-${matched[2]}-${matched[3]} ${matched[4]}:${matched[5]}:${matched[6]}`;
}
const pluginStatusOptions = [
  { label: "已发现", value: "discovered", tagType: "info" },
  { label: "已安装", value: "installed", tagType: "success" },
  { label: "待升级", value: "pending_upgrade", tagType: "warning" },
  { label: "异常", value: "error", tagType: "danger" }
];

const operationLogStatusOptions = [
  { label: "成功", value: "success", tagType: "success" },
  { label: "失败", value: "failed", tagType: "danger" },
  { label: "阻塞", value: "blocked", tagType: "warning" },
  { label: "预演", value: "dry_run", tagType: "info" }
];

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    pluginId: undefined,
    pluginName: undefined,
    enabled: undefined,
    status: undefined,
    source: undefined
  },
  operationLogQueryParams: {
    pageNum: 1,
    pageSize: 10,
    pluginId: undefined,
    operation: undefined,
    status: undefined
  }
});

const { queryParams, operationLogQueryParams } = toRefs(data);

const planTargetSummary = computed(() => {
  return selectedPluginIds.value.length ? `已选 ${selectedPluginIds.value.length} 个插件` : "请选择插件";
});

/** 查询插件列表 */
function getList() {
  loading.value = true;
  listPlugin(queryParams.value).then(response => {
    pluginList.value = response.rows;
    total.value = response.total;
    selectedPluginIds.value = [];
  }).catch(error => {
    feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    loading.value = false;
  });
}

/** 多选框选中数据 */
function handleSelectionChange(selection) {
  selectedPluginIds.value = selection.map(item => item.pluginId).filter(Boolean);
}

function togglePluginSelection(row, checked) {
  selectedPluginIds.value = checked
    ? [...new Set([...selectedPluginIds.value, row.pluginId])]
    : selectedPluginIds.value.filter(pluginId => pluginId !== row.pluginId);
}

function toggleAllPlugins(event) {
  selectedPluginIds.value = event.target.checked ? selectablePlugins.value.map(row => row.pluginId) : [];
}

function canSelectForBatch(row) {
  return !isOrphanPlugin(row);
}

/** 清空当前选择 */
function clearSelectedPlugins() {
  selectedPluginIds.value = [];
  pluginTableRef.value?.clearSelection?.();
}

/** 搜索按钮操作 */
function handleQuery() {
  queryParams.value.pageNum = 1;
  clearSelectedPlugins();
  getList();
}

/** 重置按钮操作 */
function resetQuery() {
  proxy.resetForm("queryRef");
  handleQuery();
}

/** 插件启停状态修改 */
function handleEnabledChange(row) {
  const enabled = row.enabled === "0";
  const operation = getEnabledSwitchOperation(row);
  if (isOperationBlocked(row, operation)) {
    row.enabled = row.enabled === "0" ? "1" : "0";
    feedback.msgWarning(getCapabilityReason(row.capability));
    return;
  }
  const text = enabled ? "启用" : "停用";
  feedback.confirm('确认要"' + text + '""' + row.pluginName + '"插件吗?').then(function () {
    return enabled ? enablePlugin(row.pluginId) : disablePlugin(row.pluginId);
  }).then(() => {
    feedback.msgSuccess(text + "成功");
    getList();
  }).catch(function (error) {
    row.enabled = row.enabled === "0" ? "1" : "0";
    if (isUserCancel(error)) {
      return;
    }
    showActionResult("插件" + text + "失败", buildErrorActionResult(error, {
      pluginId: row.pluginId,
      operation: enabled ? "enable" : "disable"
    }), {});
  });
}

function getEnabledSwitchOperation(row) {
  return row?.enabled === "0" ? "disable" : "enable";
}

function isEnabledSwitchBlocked(row) {
  const triesToEnableErrorPlugin = row?.status === "error" && row?.enabled !== "0";
  return triesToEnableErrorPlugin || isOperationBlocked(row, getEnabledSwitchOperation(row));
}

function getEnabledSwitchTooltip(row) {
  if (row?.status === "error") {
    return row?.enabled === "0"
      ? "插件当前被异常状态隔离；关闭开关可取消自动恢复意图"
      : "插件处于异常状态，请重新安装或升级修复，不能直接启用";
  }
  if (isOperationBlocked(row, getEnabledSwitchOperation(row))) {
    return getCapabilityReason(row.capability);
  }
  return "";
}

/** 打开详情 */
function handleDetail(row) {
  loading.value = true;
  migrationHistory.value = [];
  getPlugin(row.pluginId).then(response => {
    detail.value = response.data;
    detailOpen.value = true;
    loadPluginMigrationHistory(row.pluginId);
  }).catch(error => {
    feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    loading.value = false;
  });
}

/** 查询插件 migration 历史 */
function loadPluginMigrationHistory(pluginId) {
  if (!pluginId) {
    migrationHistory.value = [];
    return Promise.resolve();
  }
  migrationLoading.value = true;
  return listPluginMigrations(pluginId).then(response => {
    migrationHistory.value = response.data?.migrations || [];
  }).catch(error => {
    migrationHistory.value = [];
    feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    migrationLoading.value = false;
  });
}

/** 人工标记 migration 成功 */
function handleMarkMigrationSuccess(row) {
  const pluginId = detail.value.pluginId;
  const migrationPath = row?.migrationPath;
  if (!pluginId || !migrationPath) {
    return;
  }
  feedback.confirm('确认要将"' + migrationPath + '"标记为执行成功吗?').then(function () {
    migrationLoading.value = true;
    return markPluginMigrationSuccess(pluginId, {
      migrationPath,
      note: "管理页人工标记为成功"
    });
  }).then(response => {
    feedback.msgSuccess(response.data?.message || "migration 已标记为成功");
    return loadPluginMigrationHistory(pluginId);
  }).then(() => {
    getList();
  }).catch(error => {
    if (!isUserCancel(error)) {
      feedback.msgError(getErrorMessage(error));
    }
  }).finally(() => {
    migrationLoading.value = false;
  });
}

/** 人工标记 migration 失败 */
function handleMarkMigrationFailed(row) {
  const pluginId = detail.value.pluginId;
  const migrationPath = row?.migrationPath;
  if (!pluginId || !migrationPath) {
    return;
  }
  feedback.confirm('确认要将"' + migrationPath + '"标记为失败并允许后续重试吗?').then(function () {
    migrationLoading.value = true;
    return markPluginMigrationFailed(pluginId, {
      migrationPath,
      note: "管理页人工标记为失败"
    });
  }).then(response => {
    feedback.msgSuccess(response.data?.message || "migration 已标记为失败");
    return loadPluginMigrationHistory(pluginId);
  }).then(() => {
    getList();
  }).catch(error => {
    if (!isUserCancel(error)) {
      feedback.msgError(getErrorMessage(error));
    }
  }).finally(() => {
    migrationLoading.value = false;
  });
}

/** 打开插件配置 */
function handleConfig(row) {
  const generation = ++configGeneration;
  const pluginId = row.pluginId;
  configLoading.value = true;
  configPluginId.value = pluginId;
  configTitle.value = row.pluginName + "配置";
  configItems.value = [];
  getPluginConfig(pluginId).then(response => {
    if (generation !== configGeneration || pluginId !== configPluginId.value) return;
    const configs = response.data?.configs || [];
    configItems.value = configs;
    configOpen.value = true;
  }).catch(error => {
    if (generation === configGeneration) feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    if (generation === configGeneration) configLoading.value = false;
  });
}

/** 保存插件配置 */
function submitConfig(values) {
  const generation = configGeneration;
  const pluginId = configPluginId.value;
  configLoading.value = true;
  updatePluginConfig(pluginId, { values }).then(() => {
    if (generation !== configGeneration || pluginId !== configPluginId.value) return;
    feedback.msgSuccess("插件配置已保存");
    configOpen.value = false;
  }).catch(error => {
    if (generation === configGeneration) feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    if (generation === configGeneration) configLoading.value = false;
  });
}

/** 检查插件 */
function handleCheck(row) {
  actionLoading.value = true;
  checkPlugin(row.pluginId).then(response => {
    showActionResult("插件检查结果", response.data, {});
  }).catch(error => {
    showActionResult("插件检查失败", buildErrorActionResult(error, {
      pluginId: row.pluginId,
      operation: "check"
    }), {});
  }).finally(() => {
    actionLoading.value = false;
  });
}

/** 执行插件健康检查 */
function handleHealth(row) {
  const generation = ++diagnosticGeneration;
  const pluginId = row.pluginId;
  diagnosticPluginId.value = pluginId;
  diagnosticLoading.value = true;
  diagnosticTitle.value = row.pluginName + "健康检查";
  diagnosticResult.value = {
    pluginId: row.pluginId,
    message: "正在执行插件健康检查..."
  };
  diagnosticOpen.value = true;
  healthPlugin(pluginId).then(response => {
    if (generation !== diagnosticGeneration || pluginId !== diagnosticPluginId.value) return;
    diagnosticResult.value = response.data || {};
  }).catch(error => {
    if (generation !== diagnosticGeneration || pluginId !== diagnosticPluginId.value) return;
    diagnosticResult.value = {
      ok: false,
      pluginId: row.pluginId,
      message: getErrorMessage(error),
      error: serializeError(error)
    };
  }).finally(() => {
    if (generation === diagnosticGeneration) diagnosticLoading.value = false;
  });
}

/** 生成插件诊断包 */
function handleDiagnose(row) {
  const generation = ++diagnosticGeneration;
  const pluginId = row.pluginId;
  diagnosticPluginId.value = pluginId;
  diagnosticLoading.value = true;
  diagnosticTitle.value = row.pluginName + "诊断包";
  diagnosticResult.value = {
    pluginId: row.pluginId,
    message: "正在生成插件诊断包..."
  };
  diagnosticOpen.value = true;
  diagnosePlugin(pluginId).then(response => {
    if (generation !== diagnosticGeneration || pluginId !== diagnosticPluginId.value) return;
    diagnosticResult.value = response.data || {};
  }).catch(error => {
    if (generation !== diagnosticGeneration || pluginId !== diagnosticPluginId.value) return;
    diagnosticResult.value = {
      ok: false,
      pluginId: row.pluginId,
      message: getErrorMessage(error),
      error: serializeError(error)
    };
  }).finally(() => {
    if (generation === diagnosticGeneration) diagnosticLoading.value = false;
  });
}

/** 检查插件依赖 */
function handleDependencies(row) {
  const generation = ++dependencyGeneration;
  const pluginId = row.pluginId;
  dependencyLoading.value = true;
  dependencyPluginId.value = pluginId;
  dependencyResult.value = {};
  checkPluginDependencies(pluginId).then(response => {
    if (generation !== dependencyGeneration || pluginId !== dependencyPluginId.value) return;
    dependencyResult.value = response.data || {};
    dependencyOpen.value = true;
  }).catch(error => {
    if (generation !== dependencyGeneration || pluginId !== dependencyPluginId.value) return;
    dependencyResult.value = {
      ok: false,
      pluginId: row.pluginId,
      message: getErrorMessage(error),
      dependencies: [],
      plan: []
    };
    dependencyOpen.value = true;
  }).finally(() => {
    if (generation === dependencyGeneration) dependencyLoading.value = false;
  });
}

/** 生成插件依赖安装计划 */
function handleDependencyDryRun() {
  if (isCapabilityOperationBlocked(dependencyResult.value.capability, "dependency_install")) {
    feedback.msgWarning(getCapabilityReason(dependencyResult.value.capability));
    return;
  }
  const generation = dependencyGeneration;
  const pluginId = dependencyPluginId.value;
  dependencyLoading.value = true;
  installPluginDependencies(pluginId, true).then(response => {
    if (generation !== dependencyGeneration || pluginId !== dependencyPluginId.value) return;
    dependencyResult.value = response.data || {};
  }).catch(error => {
    if (generation === dependencyGeneration) feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    if (generation === dependencyGeneration) dependencyLoading.value = false;
  });
}

/** 执行插件依赖安装 */
function handleDependencyInstall() {
  if (isCapabilityOperationBlocked(dependencyResult.value.capability, "dependency_install")) {
    feedback.msgWarning(getCapabilityReason(dependencyResult.value.capability));
    return;
  }
  if (dependencyResult.value.policy?.allowed === false) {
    feedback.msgWarning(getDependencyPolicyReason(dependencyResult.value.policy));
    return;
  }
  const generation = dependencyGeneration;
  const pluginId = dependencyPluginId.value;
  feedback.confirm('确认要安装"' + pluginId + '"插件缺失依赖吗?').then(function () {
    dependencyLoading.value = true;
    return installPluginDependencies(pluginId, false);
  }).then(response => {
    if (generation !== dependencyGeneration || pluginId !== dependencyPluginId.value) return;
    dependencyResult.value = response.data || {};
    feedback.msgSuccess("插件依赖安装完成");
  }).catch(error => {
    if (generation === dependencyGeneration && !isUserCancel(error)) {
      feedback.msgError(getErrorMessage(error));
    }
  }).finally(() => {
    if (generation === dependencyGeneration) dependencyLoading.value = false;
  });
}

/** 生成插件批量操作拓扑计划 */
function handlePlan(operation) {
  if (!selectedPluginIds.value.length) {
    feedback.msgWarning("请先选择要操作的插件");
    return;
  }
  planLoading.value = true;
  planTitle.value = "插件" + formatPluginOperation(operation) + "计划";
  const pluginIds = getBatchTargetPluginIds();
  batchResult.value = normalizePluginBatchResponse({});
  planPlugins(operation, pluginIds).then(response => {
    planResult.value = normalizePluginPlanResponse(response.data);
    planOpen.value = true;
  }).catch(error => {
    feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    planLoading.value = false;
  });
}

/** 获取批量目标插件ID */
function getBatchTargetPluginIds() {
  return [...selectedPluginIds.value];
}

/** 执行插件批量操作 */
function handleExecuteBatch(dryRun) {
  if (!(planResult.value.ok && planResult.value.executablePluginIds.length > 0)) {
    return;
  }
  const operation = planResult.value.operation;
  const pluginIds = planResult.value.executablePluginIds;
  feedback.confirm('确认要批量执行插件' + formatPluginOperation(operation) + '吗? 目标：' + formatPlanDependencies(pluginIds)).then(function () {
    planLoading.value = true;
    return batchPlugins(operation, pluginIds, dryRun, batchContinueOnError.value);
  }).then(response => {
    batchResult.value = normalizePluginBatchResponse(response.data);
    planResult.value = normalizePluginPlanResponse(response.data);
    feedback.msgSuccess(response.data?.message || "插件批量操作完成");
    getList();
  }).catch(error => {
    if (isUserCancel(error)) {
      return;
    }
    batchResult.value = normalizePluginBatchResponse(buildErrorBatchResult(error, operation));
  }).finally(() => {
    planLoading.value = false;
  });
}

/** 打开插件操作审计 */
function handleOperationLog() {
  resetOperationLogSearchState();
  resetOperationLogRetentionState();
  operationLogOpen.value = true;
  loadOperationLogRetentionPolicy();
  getOperationLogList();
}

/** 搜索插件操作审计 */
function handleOperationLogQuery() {
  operationLogQueryParams.value.pageNum = 1;
  getOperationLogList();
}

/** 重置插件操作审计查询 */
function resetOperationLogQuery() {
  resetOperationLogSearchState();
  handleOperationLogQuery();
}

function resetOperationLogSearchState() {
  operationLogDateRange.value = [];
  operationLogQueryParams.value.pageNum = 1;
  operationLogQueryParams.value.pluginId = undefined;
  operationLogQueryParams.value.operation = undefined;
  operationLogQueryParams.value.status = undefined;
}

function resetOperationLogRetentionState() {
  operationLogRetentionLoading.value = false;
  operationLogRetentionResult.value = {};
  operationLogRetentionForm.retentionDays = operationLogRetentionDefaultDays.value;
}

function buildOperationLogQueryParams() {
  return proxy.addDateRange({ ...operationLogQueryParams.value }, operationLogDateRange.value);
}

function loadOperationLogRetentionPolicy() {
  getConfigKey(OPERATION_LOG_RETENTION_CONFIG_KEY).then(response => {
    const retentionDays = Number(response.msg);
    if (Number.isInteger(retentionDays) && retentionDays >= 0) {
      operationLogRetentionDefaultDays.value = retentionDays;
      operationLogRetentionForm.retentionDays = retentionDays;
    }
  }).catch(() => {});
}

/** 查询插件操作审计列表 */
function getOperationLogList() {
  operationLogLoading.value = true;
  listPluginOperationLog(buildOperationLogQueryParams()).then(response => {
    operationLogList.value = response.rows;
    operationLogTotal.value = response.total;
  }).catch(error => {
    feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    operationLogLoading.value = false;
  });
}

/** 打开插件操作审计详情 */
function handleOperationLogDetail(row) {
  getPluginOperationLog(row.operationId).then(response => {
    operationLogDetail.value = normalizePluginOperationLogDetail(response.data || {});
    operationLogDetailOpen.value = true;
  }).catch(error => {
    feedback.msgError(getErrorMessage(error));
  });
}

/** 导出插件操作审计 */
function handleOperationLogExport() {
  operationLogExportLoading.value = true;
  proxy.download("system/plugin/operation-log/export", {
    ...buildOperationLogQueryParams(),
    exportLimit: 5000
  }, `plugin_operation_log_${new Date().getTime()}.xlsx`).finally(() => {
    operationLogExportLoading.value = false;
  });
}

/** 预览插件操作审计保留策略 */
function handleOperationLogRetentionPreview() {
  operationLogRetentionLoading.value = true;
  retainPluginOperationLog({
    retentionDays: operationLogRetentionForm.retentionDays,
    dryRun: true
  }).then(response => {
    operationLogRetentionResult.value = response.data || {};
  }).catch(error => {
    feedback.msgError(getErrorMessage(error));
  }).finally(() => {
    operationLogRetentionLoading.value = false;
  });
}

/** 清理插件操作审计历史 */
function handleOperationLogRetentionClean() {
  feedback.confirm(buildOperationLogRetentionConfirmMessage()).then(function () {
    operationLogRetentionLoading.value = true;
    return retainPluginOperationLog({
      retentionDays: operationLogRetentionForm.retentionDays,
      dryRun: false
    });
  }).then(response => {
    operationLogRetentionResult.value = response.data || {};
    feedback.msgSuccess("插件操作审计清理完成");
    getOperationLogList();
  }).catch(error => {
    if (!isUserCancel(error)) {
      feedback.msgError(getErrorMessage(error));
    }
  }).finally(() => {
    operationLogRetentionLoading.value = false;
  });
}

function buildOperationLogRetentionConfirmMessage() {
  if (Number(operationLogRetentionForm.retentionDays) === 0) {
    return "确认清理当前时间之前的全部插件操作审计日志吗?";
  }

  return '确认清理超过"' + operationLogRetentionForm.retentionDays + '"天的插件操作审计日志吗?';
}

/** 安装插件预演 */
function handleInstallDryRun(row) {
  actionLoading.value = true;
  installPlugin(row.pluginId, true).then(response => {
    showActionResult("插件安装预演", response.data, {
      type: "install",
      label: "安装",
      pluginId: row.pluginId,
      pluginName: row.pluginName
    });
  }).catch(error => {
    showActionResult("插件安装预演失败", buildErrorActionResult(error, {
      pluginId: row.pluginId,
      operation: "install"
    }), {});
  }).finally(() => {
    actionLoading.value = false;
  });
}

/** 升级插件预演 */
function handleUpgradeDryRun(row) {
  actionLoading.value = true;
  upgradePlugin(row.pluginId, true).then(response => {
    showActionResult("插件升级预演", response.data, {
      type: "upgrade",
      label: "升级",
      pluginId: row.pluginId,
      pluginName: row.pluginName
    });
  }).catch(error => {
    showActionResult("插件升级预演失败", buildErrorActionResult(error, {
      pluginId: row.pluginId,
      operation: "upgrade"
    }), {});
  }).finally(() => {
    actionLoading.value = false;
  });
}

/** 安全卸载插件预演 */
function handleUninstallDryRun(row) {
  actionLoading.value = true;
  uninstallPlugin(row.pluginId, true).then(response => {
    showActionResult("插件卸载预演", response.data, {
      type: "uninstall",
      label: "卸载",
      pluginId: row.pluginId,
      pluginName: row.pluginName
    });
  }).catch(error => {
    showActionResult("插件卸载预演失败", buildErrorActionResult(error, {
      pluginId: row.pluginId,
      operation: "uninstall"
    }), {});
  }).finally(() => {
    actionLoading.value = false;
  });
}

/** 物理清理孤儿插件元数据预演 */
function handlePurgeDryRun(row) {
  actionLoading.value = true;
  purgePlugin(row.pluginId, true).then(response => {
    showActionResult("孤儿插件清理预演", response.data, {
      type: "purge",
      label: "清理孤儿元数据",
      pluginId: row.pluginId,
      pluginName: row.pluginName
    });
  }).catch(error => {
    showActionResult("孤儿插件清理预演失败", buildErrorActionResult(error, {
      pluginId: row.pluginId,
      operation: "purge"
    }), {});
  }).finally(() => {
    actionLoading.value = false;
  });
}

/** 执行预演后的插件操作 */
function handleExecutePendingAction() {
  if (!pendingAction.value.type) {
    return;
  }
  const action = pendingAction.value;
  feedback.confirm('确认要执行"' + action.pluginName + '"插件' + action.label + '吗?').then(function () {
    actionLoading.value = true;
    const requestMap = {
      install: installPlugin,
      upgrade: upgradePlugin,
      uninstall: uninstallPlugin,
      purge: purgePlugin
    };
    const request = requestMap[action.type];
    return request(action.pluginId, false);
  }).then(response => {
    showActionResult("插件" + action.label + "结果", response.data, {});
    feedback.msgSuccess("插件" + action.label + "完成");
    getList();
  }).catch(error => {
    if (isUserCancel(error)) {
      return;
    }
    showActionResult("插件" + action.label + "失败", buildErrorActionResult(error, {
      pluginId: action.pluginId,
      operation: action.type
    }), {});
  }).finally(() => {
    actionLoading.value = false;
  });
}

/** 展示插件操作结果 */
function showActionResult(title, result, nextAction) {
  actionTitle.value = title;
  actionResult.value = normalizeActionResult(result);
  pendingAction.value = canExecuteActionResult(actionResult.value) ? nextAction : {};
  actionOpen.value = true;
}

/** 从详情进入异常插件的重新安装修复流程 */
function handleRepairFromDetail() {
  const plugin = { ...detail.value };
  detailOpen.value = false;
  handleInstallDryRun(plugin);
}

function getStatusLabel(status) {
  const option = pluginStatusOptions.find(item => item.value === status);
  return option ? option.label : status || "-";
}

function getStatusTagType(status) {
  const option = pluginStatusOptions.find(item => item.value === status);
  return option ? option.tagType : "info";
}

function canInstall(row) {
  return !isOrphanPlugin(row) && (!row.installedVersion || row.status === "discovered" || row.status === "error");
}

function canUpgrade(row) {
  return !isOrphanPlugin(row) && row.status === "pending_upgrade";
}

function canUninstall(row) {
  return !isOrphanPlugin(row) && row.installedVersion && row.enabled === "0";
}

function isOrphanPlugin(row) {
  return row?.source === "orphan";
}

function canExecuteActionResult(result) {
  return !result.error && !isCapabilityOperationBlocked(result.capability, result.operation) && result.dryRun && result.structureOk !== false && result.menuConflictOk !== false;
}

function isOperationBlocked(row, operation) {
  return isCapabilityOperationBlocked(row?.capability, operation);
}

function isCapabilityOperationBlocked(capability, operation) {
  return Array.isArray(capability?.blockedOperations) && capability.blockedOperations.includes(operation);
}

function getCapabilityReason(capability) {
  return capability?.primaryReason || capability?.warnings?.[0] || "当前环境不允许执行该插件操作";
}

function getDependencyPolicyReason(policy) {
  const messages = [
    ...(Array.isArray(policy?.reasons) ? policy.reasons : []),
    ...(Array.isArray(policy?.requirements) ? policy.requirements : [])
  ];
  return messages[0] || "当前依赖安装策略不允许执行真实安装";
}

function getOperationTooltip(row, operation, label) {
  return isOperationBlocked(row, operation) ? getCapabilityReason(row.capability) : label;
}

function getDependencyTagType(row) {
  if (row.status === "skipped") {
    return "info";
  }
  return row.ok ? "success" : "danger";
}

function formatDependencyInstalled(row) {
  if (row.status === "skipped") {
    return "不适用";
  }
  return row.installed ? "是" : "否";
}

function formatDependencyVersionSatisfied(row) {
  if (row.status === "skipped") {
    return "不适用";
  }
  return row.versionSatisfied ? "是" : "否";
}

function normalizeActionResult(result) {
  return normalizePluginActionResult(result);
}

function buildErrorActionResult(error, context = {}) {
  const message = getErrorMessage(error);
  const payload = error?.response?.data?.data || {};
  const migrationRecovery = payload.migrationRecovery;
  return {
    ok: false,
    pluginId: payload.pluginId || context.pluginId,
    operation: payload.operation || context.operation,
    dependencyOk: false,
    structureOk: undefined,
    menuConflictOk: undefined,
    failedStep: payload.failedStep,
    migrationRecovery,
    error: {
      message: payload.error || message,
      suggestion: migrationRecovery?.suggestion || "请先执行插件检查，确认目录结构、依赖和菜单权限冲突后再重试。",
      raw: serializeError(error)
    },
    message
  };
}

function buildErrorBatchResult(error, operation) {
  const message = getErrorMessage(error);
  const executablePluginIds = planResult.value.executablePluginIds;
  return {
    ok: false,
    operation,
    message,
    summary: {
      total: executablePluginIds.length,
      succeeded: 0,
      failed: 1,
      skipped: Math.max(executablePluginIds.length - 1, 0)
    },
    executed: [
      {
        pluginId: executablePluginIds[0] || "-",
        operation,
        ok: false,
        status: "failed",
        message,
        suggestion: "请查看审计详情或后端日志后重试。"
      }
    ]
  };
}

function getErrorMessage(error) {
  if (!error) {
    return "插件操作失败";
  }
  if (typeof error === "string") {
    return error;
  }
  return error.message || error.msg || error.response?.data?.msg || "插件操作失败";
}

function serializeError(error) {
  if (!error) {
    return {};
  }
  if (typeof error === "string") {
    return { message: error };
  }
  return {
    message: error.message,
    name: error.name,
    status: error.response?.status,
    data: error.response?.data
  };
}

function isUserCancel(error) {
  return error === "cancel" || error === "close" || error?.message === "cancel";
}

function formatBoolean(value) {
  if (value === undefined || value === null) {
    return "-";
  }
  return value ? "通过" : "异常";
}

function formatPlanDependencies(dependencies) {
  return Array.isArray(dependencies) && dependencies.length ? dependencies.join(", ") : "-";
}

function formatPluginIdsForDisplay(pluginIds) {
  const ids = Array.isArray(pluginIds) ? pluginIds.filter(Boolean) : [];
  if (!ids.length) {
    return "-";
  }
  const visibleIds = ids.slice(0, 8).join(", ");
  return ids.length > 8 ? visibleIds + " 等 " + ids.length + " 个插件" : visibleIds;
}

function formatConfigDefaultValue(item) {
  const value = item.default;
  if (item.secret && value !== undefined && value !== null && value !== "") {
    return "******";
  }
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatConfigConstraint(item) {
  const constraints = [];
  if (item.min !== undefined && item.min !== null) {
    constraints.push("最小值 " + item.min);
  }
  if (item.max !== undefined && item.max !== null) {
    constraints.push("最大值 " + item.max);
  }
  if (item.pattern) {
    constraints.push("正则 " + item.pattern);
  }
  if (Array.isArray(item.options) && item.options.length) {
    constraints.push("选项 " + item.options.map(formatConfigOption).join(", "));
  }
  return constraints.length ? constraints.join("; ") : "-";
}

function formatConfigOption(option) {
  if (!option || typeof option !== "object") {
    return String(option);
  }
  const value = option.value === undefined || option.value === null ? "" : String(option.value);
  return option.label ? option.label + "(" + value + ")" : value;
}

function formatPluginOperation(operation) {
  return getPlanOperationLabel(operation, plugin_operation_type.value);
}

function getOperationLogStatusLabel(status) {
  const option = operationLogStatusOptions.find(item => item.value === status);

  return option ? option.label : status || "-";
}

function getOperationLogStatusTagType(status) {
  const option = operationLogStatusOptions.find(item => item.value === status);

  return option ? option.tagType : "info";
}

function formatRetentionMessage(result) {
  const actionText = result.dryRun ? "预览" : "清理";
  const cutoffTime = formatPluginTime(result.cutoffTime);
  return actionText + "完成：保留 " + result.retentionDays + " 天，截止 " + cutoffTime + "，匹配 " + result.matchedCount + " 条，已删除 " + result.deletedCount + " 条";
}

function formatActionPlanState(action) {
  if (action.ok === true) {
    return "通过";
  }
  if (action.ok === false) {
    return "异常";
  }
  if (action.hook) {
    return action.hook;
  }
  if (action.targetEnabled !== undefined) {
    return action.targetEnabled ? "目标启用" : "目标停用";
  }
  if (action.targetStatus !== undefined) {
    return action.targetStatus === "0" ? "目标正常" : "目标停用";
  }
  return "-";
}

function formatJson(value) {
  return JSON.stringify(value || {}, null, 2);
}

onBeforeUnmount(() => {
  configGeneration += 1;
  dependencyGeneration += 1;
  diagnosticGeneration += 1;
});
getList();
</script>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}

.mr12 {
  margin-right: 12px;
}

.plugin-selection-tag {
  height: 32px;
  line-height: 30px;
}

.plugin-switch-tooltip-target {
  display: inline-flex;
  align-items: center;
}

.plugin-action-buttons {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.plugin-action-buttons :deep(.ant-btn) {
  margin-left: 0;
}

.plugin-query-form :deep(.ant-input),
.plugin-query-form :deep(.ant-select) {
  width: 220px;
}

.plugin-audit-drawer {
  display: flex;
  min-height: 100%;
  flex-direction: column;
}

.plugin-audit-search {
  padding: 12px 12px 4px;
  margin-bottom: 12px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}

.plugin-audit-search {
  background: #fff;
}

.plugin-audit-maintenance {
  margin-bottom: 12px;
  background: #f8f9fb;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}

.plugin-audit-maintenance :deep(.ant-collapse-header) {
  height: 40px;
  padding: 0 12px;
  background: transparent;
  border-bottom-color: transparent;
}

.plugin-audit-maintenance :deep(.ant-collapse-content) {
  background: transparent;
  border-bottom: 0;
}

.plugin-audit-maintenance :deep(.ant-collapse-content-box) {
  padding: 0 12px 12px;
}

.plugin-audit-maintenance-title {
  margin-right: 8px;
  color: #606266;
  font-weight: 600;
}

.plugin-log-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
}

.plugin-log-toolbar :deep(.ant-form-item) {
  margin-bottom: 10px;
}

.plugin-audit-date-range :deep(.ant-picker) {
  width: 220px;
}

.plugin-audit-table {
  flex: 1;
  min-height: 0;
}

.plugin-audit-table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.plugin-audit-table-actions {
  flex: 1;
}

.drawer-footer {
  margin-top: 16px;
  text-align: right;
}

@media (max-width: 768px) {
  .plugin-audit-table-toolbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
  }
}
</style>
