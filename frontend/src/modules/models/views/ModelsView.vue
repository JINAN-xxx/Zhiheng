<!-- Adapted from original model center, MIT licensed. -->
<script setup lang="ts">
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  InputPassword as AInputPassword,
  Drawer as ADrawer,
  Tabs as ATabs,
  TabPane as ATabPane,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  Select as ASelect,
  Tag as ATag,
  Switch as ASwitch
} from 'ant-design-vue'
import { ref } from 'vue'
import { PlusOutlined, CopyOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ModelRatesDialog from '../components/ModelRatesDialog.vue'
import ModelActions from '../components/ModelActions.vue'
import ModelCatalog from '../components/ModelCatalog.vue'
import ModelConnectionCatalog from '../components/ModelConnectionCatalog.vue'
import ModelDetailModal from '../components/ModelDetailModal.vue'
import ModelTrialDialog from '../components/ModelTrialDialog.vue'
import type { ChatModel } from '../api/index.ts'
import { useModelDirectory } from '../composables/useModelDirectory.ts'
const {
  credentialPage,
  canCreateModels,
  canUpdateModels,
  canDeleteModels,
  canToggleModels,
  canCreateCredentials,
  canUpdateCredentials,
  canDeleteCredentials,
  canToggleCredentials,
  canReadModels,
  canWriteModels,
  canReadCredentials,
  canWriteCredentials,
  canTestModels,
  denyTrialAccess,
  canReadRates,
  canWriteRates,
  setActiveTab,
  activeTab,
  modelSearch,
  modelType,
  modelFilters,
  isMobile,
  lastSuccessAt,
  visibleModelColumns,
  connections,
  models,
  filteredModels,
  connectionEnabled,
  connectionDisabled,
  loading,
  saving,
  error,
  errorDetails,
  modalError,
  connectionOpen,
  modelOpen,
  editingConnection,
  editingModel,
  detailModel,
  rateModel,
  connectionForm,
  modelForm,
  testingModel,
  testBusy,
  connectionOptions,
  load,
  copyModelCode,
  openConnection,
  openModel,
  closeConnection,
  saveConnection,
  closeModel,
  setConnectionId,
  setMaxOutputTokens,
  setModelType,
  modelTypeLabel,
  changeModel,
  changeConnection,
  saveModel,
  testModel,
  modelPending,
  modelErrorMessages,
  connectionLabels,
  connectionIssues,
  detailPending,
  detailError,
  detailErrorDetails,
  detailConnectionLabel,
  detailConnectionIssue,
  openDetails,
  setDetailOpen,
  toggleModel,
  toggleDetails,
  copyDetails,
  editDetails,
  ratesDetails,
  testDetails,
  controlDetails
} = useModelDirectory()
const trial = ref<InstanceType<typeof ModelTrialDialog> | null>(null)
function openTrial(model: ChatModel) {
  if (trial.value?.open(model)) setDetailOpen(false)
}
function trialDetails() {
  if (detailModel.value) openTrial(detailModel.value)
}
</script>
<template>
  <main class="app-container ai-model-center">
    <WorkspaceToolbar :title="credentialPage ? '凭据管理' : '模型目录'"
      ><template #actions
        ><RefreshButton label="刷新当前视图" :loading="loading" @click="load" /><AButton
          v-if="canCreateModels && activeTab === 'platform'"
          type="primary"
          aria-label="接入模型"
          :disabled="!connectionOptions.length"
          @click="openModel()"
          ><PlusOutlined />接入模型</AButton
        ><AButton
          v-if="canCreateCredentials && activeTab === 'credentials'"
          type="primary"
          aria-label="新增凭据"
          :disabled="saving"
          @click="openConnection()"
          ><PlusOutlined />新增凭据</AButton
        ></template
      ></WorkspaceToolbar
    >
    <section class="model-workspace">
      <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" />
      <ResourceReferences :details="errorDetails" />
      <ATabs :active-key="activeTab" class="workspace-tabs" @update:active-key="setActiveTab">
        <ATabPane v-if="!credentialPage" key="platform" tab="平台模型"
          ><section class="workspace-panel">
            <div class="model-catalog-toolbar">
              <div class="model-filters" aria-label="模型类型筛选">
                <button
                  v-for="filter in modelFilters"
                  :key="filter.value"
                  type="button"
                  :class="{ active: modelType === filter.value }"
                  :aria-pressed="modelType === filter.value"
                  @click="modelType = filter.value"
                >
                  {{ filter.label }}
                </button>
              </div>
              <div class="catalog-tools">
                <span v-if="!isMobile" class="catalog-total"
                  >{{ filteredModels.length }} 个模型</span
                >
                <AInput
                  v-model:value="modelSearch"
                  allow-clear
                  aria-label="搜索模型"
                  :placeholder="canReadModels ? '搜索模型名称、代码或连接' : '搜索模型名称或代码'"
                >
                  <template #prefix><SearchOutlined /></template>
                </AInput>
              </div>
            </div>
            <div v-if="isMobile" class="panel-toolbar">
              <div class="panel-summary">
                <span>{{ models.length }} 个平台模型</span
                ><span v-if="lastSuccessAt">更新于 {{ lastSuccessAt }}</span>
              </div>
            </div>
            <ModelCatalog
              v-if="!isMobile && filteredModels.length"
              :models="filteredModels"
              :connection-labels="connectionLabels"
              :connection-issues="connectionIssues"
              :errors="modelErrorMessages"
              :can-write="canToggleModels"
              :pending="modelPending"
              @open="openDetails"
              @toggle="toggleModel"
            />
            <ResourceTable
              v-else-if="filteredModels.length || loading"
              class="model-table"
              :columns="visibleModelColumns"
              :scroll-x="isMobile ? '100%' : 850"
              :rows="filteredModels"
              :loading="loading"
              ><template #bodyCell="{ column, record: model }">
                <template v-if="column.key === 'model'"
                  ><div class="mobile-model-cell">
                    <button
                      type="button"
                      class="model-display-name model-table-open"
                      :aria-label="`查看模型详情：${model.name}`"
                      @click="openDetails(model)"
                    >
                      {{ model.name }}
                    </button>
                    <div class="resource-value resource-value--secondary">
                      <span class="resource-text" :title="model.id">{{ model.id }}</span>
                      <button
                        type="button"
                        class="copy-button"
                        aria-label="复制模型代码"
                        title="复制模型代码"
                        :disabled="!model.id.trim()"
                        @click="copyModelCode(model.id)"
                      >
                        <CopyOutlined />
                      </button>
                    </div>
                    <div v-if="isMobile" class="mobile-model-meta">
                      <ATag color="blue">{{ modelTypeLabel(model.model_type) }}</ATag
                      ><ATag
                        v-if="canReadModels"
                        :color="connectionEnabled(model.connection_id) ? 'success' : 'default'"
                        >{{
                          connectionEnabled(model.connection_id) ? '连接已启用' : '连接不可用'
                        }}</ATag
                      ><ATag :color="model.enabled ? 'success' : 'default'">{{
                        model.enabled ? '已启用' : '已停用'
                      }}</ATag>
                    </div>
                  </div></template
                >
                <template v-else-if="column.key === 'type'"
                  ><ATag>{{ modelTypeLabel(model.model_type) }}</ATag></template
                >
                <template v-else-if="column.key === 'binding'"
                  ><ATag :color="connectionEnabled(model.connection_id) ? 'success' : 'default'">{{
                    connectionEnabled(model.connection_id) ? '已启用' : '不可用'
                  }}</ATag></template
                >
                <template v-else-if="column.key === 'status'"
                  ><ATag :color="model.enabled ? 'success' : 'default'">{{
                    model.enabled ? '已启用' : '已停用'
                  }}</ATag></template
                >
                <template v-else-if="column.key === 'output'">
                  {{
                    model.model_type === 'chat'
                      ? `${model.max_output_tokens.toLocaleString()} Token`
                      : '—'
                  }}
                </template>
                <template v-else-if="column.key === 'actions'">
                  <ModelActions
                    :model="model"
                    :busy="testBusy"
                    :mutating="modelPending[model.id] ?? false"
                    :testing="testingModel === model.id"
                    :can-write="canUpdateModels || canDeleteModels || canToggleModels"
                    :can-update="canUpdateModels"
                    :can-delete="canDeleteModels"
                    :can-toggle="canToggleModels"
                    :can-read-rates="canReadRates"
                    :can-test="canTestModels"
                    :test-disabled="connectionDisabled(model.connection_id)"
                    @edit="openModel(model)"
                    @rates="canReadRates && (rateModel = model)"
                    @test="testModel(model)"
                    @trial="openTrial(model)"
                    @control="changeModel(model, $event)"
                  />
                </template> </template
            ></ResourceTable>
            <WorkspaceState
              v-else
              :title="models.length ? '没有匹配的模型' : '暂无平台模型'"
              :message="
                models.length
                  ? '试试其他模型类型或关键词。'
                  : !canWriteModels
                    ? '当前暂无可用模型，请联系平台管理员。'
                    : connections.length
                      ? '点击接入模型，选择已保存的连接。'
                      : '请先在凭据管理中添加模型连接。'
              "
            /></section
        ></ATabPane>
        <ATabPane v-if="credentialPage && canReadCredentials" key="credentials" tab="凭据管理">
          <section class="workspace-panel">
            <ModelConnectionCatalog
              :connections="connections"
              :models="models"
              :loading="loading"
              :can-write="canUpdateCredentials || canDeleteCredentials || canToggleCredentials"
              :can-update="canUpdateCredentials"
              :can-delete="canDeleteCredentials"
              :can-toggle="canToggleCredentials"
              :busy="saving"
              @edit="openConnection"
              @control="changeConnection"
            />
          </section>
        </ATabPane>
      </ATabs>
    </section>
  </main>
  <ADrawer
    :open="connectionOpen && canWriteCredentials"
    :title="editingConnection ? '编辑服务商凭据' : '新建服务商凭据'"
    width="min(620px, 100vw)"
    root-class-name="model-form-drawer"
    destroy-on-close
    @close="closeConnection"
  >
    <AAlert v-if="modalError" :message="modalError" type="error" show-icon class="space-bottom" />
    <AForm layout="vertical"
      ><AFormItem label="连接名称" required
        ><AInput
          v-model:value="connectionForm.name"
          placeholder="例如：团队模型服务"
          :maxlength="80" /></AFormItem
      ><AFormItem label="API 地址" required
        ><AInput
          v-model:value="connectionForm.base_url"
          placeholder="https://api.example.com/v1"
          :maxlength="2048" /></AFormItem
      ><AFormItem label="API Key" :required="!editingConnection"
        ><AInputPassword
          v-model:value="connectionForm.api_key"
          autocomplete="off"
          :placeholder="
            editingConnection ? '留空保留当前密钥' : '密钥仅用于本次提交，保存后不会回显'
          " /></AFormItem
    ></AForm>
    <template #footer
      ><div class="footer-actions">
        <AButton @click="closeConnection">取消</AButton
        ><AButton type="primary" :loading="saving" @click="saveConnection">保存连接</AButton>
      </div></template
    >
  </ADrawer>
  <ADrawer
    :open="modelOpen && canWriteModels"
    :title="editingModel ? '编辑模型' : '接入模型'"
    width="min(840px, 100vw)"
    root-class-name="model-onboarding-drawer"
    destroy-on-close
    :mask-closable="!saving"
    :closable="!saving"
    :keyboard="!saving"
    @close="closeModel"
  >
    <AAlert v-if="modalError" :message="modalError" type="error" show-icon class="space-bottom" />
    <div class="onboarding-form">
      <section class="form-section">
        <div class="section-heading">
          <h3>模型信息</h3>
          <p>先确定模型用途，再填写服务商提供的模型名称。</p>
        </div>
        <AForm layout="vertical" :disabled="saving">
          <AFormItem label="模型类型"
            ><ARadioGroup
              :value="modelForm.model_type"
              :disabled="editingModel !== null"
              class="model-type-options"
              button-style="solid"
              @update:value="setModelType"
              ><ARadioButton value="chat">对话</ARadioButton
              ><ARadioButton value="embedding">向量</ARadioButton
              ><ARadioButton value="rerank">重排序</ARadioButton></ARadioGroup
            ></AFormItem
          >
          <AFormItem v-if="modelForm.model_type === 'chat'" label="视觉能力"
            ><ASwitch
              :checked="modelForm.vision_enabled"
              aria-label="启用视觉能力"
              @update:checked="(value) => (modelForm.vision_enabled = value === true)"
            />
            <p class="muted small">仅在上游模型支持图片输入时启用。</p></AFormItem
          >
          <div class="form-grid">
            <AFormItem label="上游模型标识" required
              ><AInput
                v-model:value="modelForm.upstream_model"
                placeholder="填写服务商提供的模型 ID"
                :maxlength="200" /></AFormItem
            ><AFormItem label="显示名称" required
              ><AInput v-model:value="modelForm.name" placeholder="例如：日常助手" :maxlength="80"
            /></AFormItem>
          </div>
        </AForm>
      </section>
      <section class="form-section">
        <div class="section-heading">
          <h3>连接配置</h3>
          <p>复用已保存的连接。</p>
        </div>
        <AForm layout="vertical" :disabled="saving"
          ><AFormItem label="模型连接" required
            ><ASelect
              :value="modelForm.connection_id || undefined"
              :options="connectionOptions"
              placeholder="选择模型连接"
              @update:value="setConnectionId" /></AFormItem
          ><AFormItem label="最大输出 tokens" required
            ><AInputNumber
              :value="modelForm.max_output_tokens ?? ''"
              :min="1"
              :precision="0"
              class="full-width"
              @update:value="setMaxOutputTokens" /></AFormItem
          ><AFormItem
            label="上下文窗口 tokens（可选）"
            extra="填写模型实际窗口；留空时使用平台输入预算。"
            ><AInputNumber
              :value="modelForm.context_window_tokens ?? ''"
              :min="256"
              :max="2097152"
              :precision="0"
              class="full-width"
              placeholder="使用平台输入预算"
              @update:value="
                modelForm.context_window_tokens = typeof $event === 'number' ? $event : null
              " /></AFormItem
        ></AForm>
      </section>
    </div>
    <template #footer
      ><div class="onboarding-footer">
        <span class="footer-status">{{ '保存前会校验服务商连接、密钥和模型标识。' }}</span>
        <div class="footer-actions">
          <AButton :disabled="saving" @click="closeModel">取消</AButton
          ><AButton type="primary" aria-label="保存模型" :loading="saving" @click="saveModel">{{
            saving ? '正在校验并保存' : '校验并保存'
          }}</AButton>
        </div>
      </div></template
    >
  </ADrawer>
  <ModelDetailModal
    :model-value="detailModel !== null"
    :model="detailModel"
    :connection-label="detailConnectionLabel"
    :connection-issue="detailConnectionIssue"
    :error="detailError"
    :error-details="detailErrorDetails"
    :can-write="canUpdateModels || canDeleteModels || canToggleModels"
    :can-update="canUpdateModels"
    :can-delete="canDeleteModels"
    :can-toggle="canToggleModels"
    :can-read-rates="canReadRates"
    :can-test="canTestModels"
    :can-read-connections="canReadModels"
    :pending="detailPending"
    :test-busy="testBusy"
    :testing="Boolean(detailModel && testingModel === detailModel.id)"
    @update:model-value="setDetailOpen"
    @toggle="toggleDetails"
    @copy="copyDetails"
    @edit="editDetails"
    @rates="ratesDetails"
    @test="testDetails"
    @trial="trialDetails"
    @control="controlDetails"
  />
  <ModelTrialDialog
    ref="trial"
    :models="models"
    :can-test="canTestModels"
    :pending="modelPending"
    :connection-issues="connectionIssues"
    @refresh="load"
    @denied="denyTrialAccess"
  />
  <ModelRatesDialog
    :model="rateModel"
    :can-read="canReadRates"
    :can-write="canWriteRates"
    @close="rateModel = null"
  />
</template>
<style scoped>
.ai-model-center {
  --ai-font-mono: var(--workspace-font-mono);
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  color: var(--color-text);
  background: var(--bg-page);
  font-family: var(--workspace-font-family);
}
.model-workspace {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 20px 24px 28px;
}
.workspace-tabs {
  min-height: 0;
  flex: 1;
}
.workspace-tabs :deep(.ant-tabs-nav) {
  margin: 0 0 20px;
  border-bottom: 1px solid var(--gray-150);
}
.workspace-tabs :deep(.ant-tabs-nav::before) {
  border-bottom: 0;
}
.workspace-tabs :deep(.ant-tabs-tab) {
  height: 40px;
  margin: 0;
  padding: 0 16px;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 400;
}
.workspace-tabs :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) {
  color: var(--main-700);
  font-weight: 600;
}
.workspace-tabs :deep(.ant-tabs-ink-bar) {
  background: var(--main-700);
}
.workspace-tabs :deep(.ant-tabs-content-holder),
.workspace-tabs :deep(.ant-tabs-content),
.workspace-tabs :deep(.ant-tabs-tabpane) {
  min-height: 0;
}
:global(.model-onboarding-drawer .ant-alert) {
  border-radius: 6px;
}
:global(.model-onboarding-drawer) {
  --ai-font-mono: var(--workspace-font-mono);
  font-family: var(--workspace-font-family);
  font-size: 14px;
}
:global(.model-onboarding-drawer .ant-drawer-content) {
  background: var(--bg-elevated);
  color: var(--color-text);
}
:global(.model-onboarding-drawer .ant-drawer-header) {
  margin-bottom: 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-elevated);
  padding-bottom: 16px;
}
:global(.model-onboarding-drawer .ant-drawer-title) {
  color: var(--color-text);
  font-size: 16px;
  font-weight: 600;
}
:global(.model-onboarding-drawer .ant-segmented),
:global(.model-onboarding-drawer .ant-btn) {
  border-radius: 6px;
}
@media (max-width: 760px) {
  :global(.model-onboarding-drawer .ant-drawer-body) {
    padding: 16px;
  }
}
@media (max-width: 640px) {
  .model-workspace {
    padding: 16px;
  }
}
@media (min-width: 761px) {
  .model-workspace {
    padding: 0 var(--workspace-space-6) 28px;
  }
  .workspace-tabs :deep(.ant-tabs-nav) {
    margin-bottom: 12px;
  }
  .ai-model-center :deep(.workspace-toolbar__subtitle) {
    margin-top: 2px;
    font-size: var(--workspace-font-size-helper);
  }
}

.model-catalog-toolbar,
.catalog-tools,
.model-filters {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-catalog-toolbar {
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin: 4px 0 20px;
}
.model-filters {
  flex-wrap: wrap;
}
.model-filters button {
  padding: 6px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 5px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.model-filters button.active {
  border-color: var(--main-200);
  background: var(--main-50);
  color: var(--main-700);
  font-weight: 600;
}
.model-filters button:focus-visible {
  outline: 2px solid var(--main-700);
  outline-offset: 2px;
}
.catalog-tools :deep(.ant-input-affix-wrapper) {
  width: 256px;
}
.catalog-total {
  color: var(--color-text-secondary);
  font-size: 13px;
  white-space: nowrap;
}
@media (max-width: 760px) {
  .model-catalog-toolbar {
    gap: 12px;
    margin-bottom: 12px;
  }
  .model-filters {
    gap: 6px;
  }
  .model-filters button {
    padding: 5px 9px;
    font-size: 12px;
  }
  .catalog-tools {
    width: 100%;
  }
  .catalog-tools :deep(.ant-input-affix-wrapper) {
    width: 100%;
  }
}
</style>

<style scoped>
.workspace-panel {
  min-width: 0;
  min-height: 360px;
  color: var(--color-text);
}
.panel-toolbar {
  display: flex;
  min-height: 54px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.panel-summary {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.panel-toolbar > :deep(.ant-btn) {
  flex-shrink: 0;
}
.workspace-panel > :deep(.ant-alert) {
  margin-bottom: 14px;
}
.model-table :deep(.ant-table) {
  font-family: var(--workspace-font-family);
  font-size: 14px;
}
.model-table :deep(.ant-table-container) {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.model-table :deep(.ant-table-thead > tr > th) {
  border-bottom-color: var(--gray-150);
  background: var(--gray-10);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
}
.model-table :deep(.ant-table-tbody > tr > td) {
  border-bottom-color: var(--gray-150);
  background: var(--bg-container);
  color: var(--color-text);
}
.model-table :deep(.ant-table-cell) {
  vertical-align: middle;
}
.model-table :deep(.ant-table-tbody > tr:hover > td) {
  background: var(--gray-50);
}
.model-table :deep(.ant-tag) {
  margin: 0;
}
.resource-value {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
}
.mobile-model-cell {
  display: grid;
  min-width: 0;
  gap: 8px;
}
.mobile-model-cell > :deep(.ant-tag) {
  justify-self: start;
}
.mobile-model-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.resource-text {
  min-width: 0;
  overflow: hidden;
  font-family: var(--workspace-font-mono);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-table-open {
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.model-display-name {
  overflow: hidden;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-value--secondary {
  color: var(--color-text-secondary);
}
.copy-button {
  display: inline-flex;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.copy-button:hover {
  background: var(--gray-10);
  color: var(--main-700);
}
.copy-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.copy-button svg {
  width: 14px;
  height: 14px;
}
.muted {
  color: var(--color-text-tertiary);
}
:deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (min-width: 761px) {
  .mobile-model-cell {
    gap: 2px;
  }
  .model-table :deep(.ant-table-cell) {
    padding: 12px;
  }
  .model-table :deep(.ant-table-tbody > tr > td) {
    font-size: var(--workspace-font-size-body);
  }
  .model-display-name {
    font-size: 14px;
  }
}
@media (max-width: 760px) {
  :deep(.ant-pagination) {
    justify-content: center;
  }
}
</style>

<style scoped>
.onboarding-form {
  color: var(--color-text);
  font-size: 14px;
}
.form-section + .form-section {
  border-top: 1px solid var(--gray-150);
  margin-top: 8px;
  padding-top: 24px;
}
.section-heading {
  margin-bottom: 20px;
}
.section-heading h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.section-heading p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.model-type-options {
  display: flex;
}
.model-type-options :deep(.ant-radio-button-wrapper) {
  flex: 1;
  text-align: center;
}
.field-description {
  margin-top: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.connection-endpoint {
  overflow-wrap: anywhere;
}
.onboarding-form :deep(.ant-input-number) {
  width: 100%;
}
.onboarding-form :deep(.engine-form) {
  padding: 0;
}
.onboarding-form :deep(.ant-form-item-label > label) {
  color: var(--color-text);
}
.advanced-settings {
  margin: 8px 0 16px;
  border: 1px solid var(--gray-150);
  background: transparent;
  border-radius: 6px;
}
.advanced-settings :deep(.ant-collapse-header) {
  color: var(--color-text);
}
.advanced-settings :deep(.ant-collapse-content-box) {
  padding: 16px 20px 0;
  background: var(--bg-container);
}
.status-message {
  margin-top: 16px;
}
.onboarding-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0;
}
.footer-status {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.footer-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}
</style>

<style scoped>
.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
