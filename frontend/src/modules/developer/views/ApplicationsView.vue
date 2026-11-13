<!-- Adapted from original application/index.vue, MIT licensed. -->
<template>
  <main class="app-container developer-api-page">
    <ApplicationApiDocs v-if="showDocs" @close="closeApiDocs" />
    <template v-else>
      <WorkspaceToolbar
        class="developer-toolbar"
        title="开发接入"
        :subtitle="showApplicationList ? '管理应用、访问密钥与接口调用权限' : ''"
      >
        <template #actions>
          <RefreshButton
            label="刷新应用"
            :loading="loading || detailLoading || keyLoading || modelLoading || usageLoading"
            @click="refreshCurrentView"
          />
          <a-button type="text" aria-label="API 说明" @click="openApiDocs"
            ><template #icon><FileTextOutlined /></template>API 说明</a-button
          >
          <a-button
            v-if="canUseCapability('developer', 'create')"
            type="primary"
            aria-label="新建应用"
            @click="openApplicationForm()"
            ><template #icon><PlusOutlined /></template>新建应用</a-button
          >
        </template>
      </WorkspaceToolbar>

      <section
        class="developer-grid"
        :class="{ 'is-list': showApplicationList, 'is-detail': !showApplicationList }"
      >
        <div v-if="showApplicationList" class="application-tools">
          <a-input
            v-model:value="query.applicationName"
            allow-clear
            aria-label="搜索应用"
            placeholder="搜索应用名称"
            @press-enter="searchApplications()"
            @update:value="(value) => value === '' && searchApplications()"
          >
            <template #prefix><SearchOutlined /></template>
          </a-input>
          <span class="application-total">共 {{ total }} 个</span>
        </div>
        <div v-if="showApplicationList" class="application-list-view" :aria-busy="loading">
          <WorkspaceState v-if="loading && !applications.length" kind="loading" />
          <WorkspaceState
            v-else-if="error && !applications.length"
            kind="error"
            :message="error"
            @retry="loadApplications()"
          />
          <WorkspaceState
            v-else-if="!applications.length"
            kind="empty"
            :title="query.applicationName.trim() ? '没有符合条件的应用' : '暂无开发者应用'"
          />
          <template v-else>
            <div v-if="error" class="stale-notice">刷新失败，当前列表可能已过期。</div>
            <div class="application-grid">
              <button
                v-for="application in applications"
                :key="application.applicationId"
                type="button"
                class="application-row"
                :aria-label="`管理应用“${application.applicationName}”`"
                :aria-describedby="`application-status-${application.applicationId}`"
                @click="selectApplication(application)"
              >
                <span class="application-icon" aria-hidden="true"><CodeOutlined /></span>
                <span class="application-row__main">
                  <strong :title="application.applicationName">{{
                    application.applicationName
                  }}</strong>
                </span>
                <span
                  :id="`application-status-${application.applicationId}`"
                  class="application-row__status"
                >
                  <WorkspaceStatus
                    :status="application.status"
                    :label="applicationStatusLabel(application.status, application.platformEnabled)"
                    :show-dot="false"
                  />
                </span>
              </button>
            </div>
            <a-pagination
              v-if="total > query.pageSize"
              v-model:current="query.pageNum"
              v-model:page-size="query.pageSize"
              :total="total"
              :page-size-options="['10', '20', '50']"
              show-size-changer
              size="small"
              @change="loadApplications()"
            />
          </template>
        </div>

        <section v-else class="application-detail">
          <a-alert v-if="error && selectedApplication" :message="error" type="error" show-icon />
          <ResourceReferences :details="errorDetails" />
          <WorkspaceState v-if="detailLoading" kind="loading" />
          <WorkspaceState
            v-else-if="error && !selectedApplication"
            kind="error"
            :message="error"
            @retry="refreshCurrentView"
          />
          <WorkspaceState
            v-else-if="!selectedApplication"
            kind="empty"
            title="选择应用以查看详情"
          />
          <template v-else>
            <a-alert
              v-if="selectedApplication.platformEnabled === false"
              type="warning"
              message="此应用已被平台停用，请联系管理员。"
              show-icon
            />
            <header class="application-context-bar">
              <a-button class="application-back" type="text" @click="backToApplicationList"
                ><LeftOutlined />应用列表</a-button
              >
              <div class="detail-identity">
                <div>
                  <h2>{{ selectedApplication.applicationName }}</h2>
                </div>
                <WorkspaceStatus
                  :status="selectedApplication.status"
                  :label="
                    applicationStatusLabel(
                      selectedApplication.status,
                      selectedApplication.platformEnabled
                    )
                  "
                  :show-dot="false"
                />
              </div>
              <div class="detail-actions">
                <a-button
                  v-if="canUseCapability('developer', 'update')"
                  type="text"
                  @click="openApplicationForm(selectedApplication)"
                  ><EditOutlined />编辑</a-button
                >
                <a-button
                  v-if="
                    canUseCapability('developer', 'toggle') &&
                    selectedApplication.status === 'active'
                  "
                  type="text"
                  danger
                  @click="disableSelectedApplication"
                  ><StopOutlined />停用</a-button
                >
                <a-button
                  v-if="
                    canUseCapability('developer', 'toggle') &&
                    selectedApplication.status === 'disabled' &&
                    selectedApplication.platformEnabled !== false
                  "
                  type="primary"
                  ghost
                  @click="enableSelectedApplication"
                  ><PlayCircleOutlined />重新启用</a-button
                >
                <a-button
                  v-if="canUseCapability('developer', 'delete')"
                  type="text"
                  danger
                  @click="deleteSelectedApplication"
                  ><DeleteOutlined />删除应用</a-button
                >
              </div>
            </header>

            <a-tabs class="detail-tabs" :active-key="activeTab" @change="changeTab">
              <a-tab-pane key="overview" tab="概览" />
              <a-tab-pane key="keys" tab="访问密钥" />
              <a-tab-pane key="models" tab="模型授权" />
              <a-tab-pane key="resources" tab="资源授权" />
              <a-tab-pane key="usage" tab="调用记录" />
            </a-tabs>

            <ApplicationOverviewPanel
              v-if="activeTab === 'overview'"
              :application="selectedApplication"
              @navigate="changeTab"
            />
            <ApplicationKeysPanel
              v-else-if="activeTab === 'keys'"
              :keys="keys"
              :scope-catalog="scopeCatalog"
              :models="models"
              :loading="keyLoading"
              :error="keyError"
              :can-list="canListKeys"
              :can-create="canCreateKey && selectedApplication.status === 'active'"
              :actions="keyActions"
              @create="openKeyForm"
              @retry="loadKeys"
              @command="handleKeyAction"
            />
            <ApplicationModelsPanel
              v-else-if="activeTab === 'models'"
              :keys="keys"
              :models="models"
              :loading="keyLoading || modelLoading"
              :error="keyError || modelError"
              :can-view="canViewModelAuthorizations"
              @retry="loadModelAuthorizations"
            />
            <ApplicationResourcesPanel
              v-else-if="activeTab === 'resources'"
              :application="selectedApplication"
              @version-change="updateResourceApplication"
            />
            <ApplicationUsagePanel
              v-else
              :rows="usageRows"
              :total="usageTotal"
              :loading="usageLoading"
              :error="usageError"
              :can-query="canQueryUsage"
              :page-num="usageQuery.pageNum"
              :page-size="usageQuery.pageSize"
              @retry="loadUsage"
              @page-change="changeUsagePage"
            />
          </template>
        </section>
      </section>

      <a-modal
        :open="applicationFormOpen"
        :title="editingApplication ? '编辑应用' : '新建应用'"
        :width="500"
        destroy-on-close
        @cancel="closeApplicationForm"
      >
        <a-alert v-if="formError" :message="formError" type="error" show-icon /><a-form
          ref="applicationFormRef"
          :model="applicationForm"
          :rules="applicationRules"
          layout="vertical"
        >
          <a-form-item label="应用名称" name="applicationName"
            ><a-input
              v-model:value="applicationForm.applicationName"
              :disabled="saving || applicationCreationUncertain"
              :maxlength="80"
              placeholder="例如内部智能工具"
          /></a-form-item>
        </a-form>
        <template #footer
          ><a-button @click="closeApplicationForm">取消</a-button
          ><a-button
            type="primary"
            :loading="saving"
            :disabled="applicationCreationUncertain"
            @click="saveApplication"
            >保存</a-button
          ></template
        >
      </a-modal>

      <a-modal
        :open="keyFormOpen"
        title="创建访问密钥"
        :width="600"
        centered
        wrap-class-name="workspace-scroll-dialog"
        destroy-on-close
        :after-close="handleKeyFormClosed"
        @cancel="closeKeyForm"
      >
        <a-alert
          v-if="formError || catalogError || modelError"
          :message="formError || catalogError || modelError"
          type="error"
          show-icon
        /><a-form ref="keyFormRef" :model="keyForm" :rules="keyRules" layout="vertical">
          <a-form-item label="密钥名称" name="keyName"
            ><a-input v-model:value="keyForm.keyName" :maxlength="100"
          /></a-form-item>
          <a-form-item
            label="操作能力"
            name="scopes"
            extra="签发后能力固定。轮换复制原权限；变更能力需签发新密钥。"
          >
            <a-spin :spinning="catalogLoading"
              ><a-checkbox-group
                :value="keyForm.scopes"
                class="scope-choices"
                @change="setKeyScopes"
              >
                <a-checkbox
                  v-for="item in scopeCatalog?.definitions ?? []"
                  :key="item.scope"
                  :value="item.scope"
                  ><span>{{ item.label }}</span
                  ><small>{{ item.description }}</small></a-checkbox
                >
              </a-checkbox-group></a-spin
            >
            <a-button v-if="catalogError" type="link" @click="loadScopeCatalog"
              >重新加载能力目录</a-button
            >
          </a-form-item>
          <a-form-item
            label="模型允许列表"
            name="modelIds"
            extra="模型或智能体调用须选模型。资源元数据管理可留空；索引和检索仍需允许相应模型。"
            ><a-select
              :value="keyForm.modelIds"
              mode="multiple"
              show-search
              option-filter-prop="label"
              style="width: 100%"
              placeholder="按所选能力选择已启用模型"
              :options="modelOptions"
              @change="setModelIds"
          /></a-form-item>
          <a-form-item label="资源每分钟请求数" name="resourceRpmLimit">
            <a-input-number
              v-if="scopeCatalog"
              :value="keyForm.resourceRpmLimit ?? ''"
              :min="scopeCatalog.resourceRpmMin"
              :max="scopeCatalog.resourceRpmMax"
              :precision="0"
              style="width: 100%"
              @change="(value) => setLimit('resourceRpmLimit', value)"
            />
          </a-form-item>
          <div class="quota-grid">
            <a-form-item label="推理每分钟请求数"
              ><a-input-number
                :value="keyForm.rpmLimit ?? ''"
                :min="1"
                style="width: 100%"
                @change="(value) => setLimit('rpmLimit', value)"
            /></a-form-item>
            <a-form-item label="每分钟令牌数"
              ><a-input-number
                :value="keyForm.tpmLimit ?? ''"
                :min="1"
                style="width: 100%"
                @change="(value) => setLimit('tpmLimit', value)"
            /></a-form-item>
            <a-form-item label="并发数"
              ><a-input-number
                :value="keyForm.concurrencyLimit ?? ''"
                :min="1"
                style="width: 100%"
                @change="(value) => setLimit('concurrencyLimit', value)"
            /></a-form-item>
          </div>
          <a-form-item
            label="月积分上限"
            name="monthlyPointsLimit"
            extra="留空仅受应用所有者积分余额约束；0 表示禁止积分调用。密钥不拥有独立余额。"
          >
            <a-input
              v-model:value="keyForm.monthlyPointsLimit"
              placeholder="可选，使用十进制积分数"
            />
          </a-form-item>
          <a-form-item label="过期时间"
            ><a-date-picker
              :value="keyForm.expiresAt ?? ''"
              value-format="YYYY-MM-DDTHH:mm:ssZ"
              show-time
              style="width: 100%"
              @change="setExpiry"
          /></a-form-item>
        </a-form>
        <template #footer
          ><a-button @click="closeKeyForm">取消</a-button
          ><a-button type="primary" :loading="saving" @click="saveKey">创建</a-button></template
        >
      </a-modal>

      <a-modal
        :open="issuedKeyOpen"
        title="访问密钥已生成"
        :width="560"
        :mask-closable="false"
        :after-close="clearIssuedKey"
        @cancel="clearIssuedKey"
      >
        <a-alert
          type="warning"
          :closable="false"
          message="该密钥仅展示一次，关闭后无法再次查看。"
          show-icon
        />
        <div class="issued-key">
          <code>{{ issuedKey?.apiKey }}</code
          ><a-button aria-label="复制访问密钥" title="复制访问密钥" @click="copyIssuedKey"
            ><template #icon><CopyOutlined /></template
          ></a-button>
        </div>
        <template #footer
          ><a-button type="primary" @click="clearIssuedKey">我已保存</a-button></template
        >
      </a-modal>
    </template>
  </main>
</template>

<script setup lang="ts">
import { canUseCapability } from '../../identity/public.ts'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  CheckboxGroup as ACheckboxGroup,
  Spin as ASpin,
  DatePicker as ADatePicker,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal as AModal,
  Pagination as APagination,
  Select as ASelect,
  Tabs as ATabs,
  TabPane as ATabPane
} from 'ant-design-vue'
import {
  CopyOutlined,
  CodeOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  LeftOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SearchOutlined,
  StopOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { applicationStatusLabel } from '../presentation.ts'
import { useApplications } from '../composables/applications.ts'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import ApplicationKeysPanel from '../components/ApplicationKeysPanel.vue'
import ApplicationModelsPanel from '../components/ApplicationModelsPanel.vue'
import ApplicationOverviewPanel from '../components/ApplicationOverviewPanel.vue'
import ApplicationUsagePanel from '../components/ApplicationUsagePanel.vue'
import ApplicationApiDocs from '../components/ApplicationApiDocs.vue'
import ApplicationResourcesPanel from '../components/ApplicationResourcesPanel.vue'
const {
  scopeCatalog,
  catalogLoading,
  catalogError,
  loadScopeCatalog,
  updateResourceApplication,
  applications,
  selectedApplication,
  keys,
  models,
  usageRows,
  total,
  usageTotal,
  loading,
  detailLoading,
  keyLoading,
  modelLoading,
  usageLoading,
  saving,
  error,
  errorDetails,
  keyError,
  modelError,
  usageError,
  formError,
  query,
  usageQuery,
  applicationFormOpen,
  applicationCreationUncertain,
  keyFormOpen,
  issuedKeyOpen,
  applicationFormRef,
  keyFormRef,
  applicationForm,
  keyForm,
  editingApplication,
  issuedKey,
  showApplicationList,
  showDocs,
  activeTab,
  canListKeys,
  canCreateKey,
  canViewModelAuthorizations,
  canQueryUsage,
  keyActions,
  modelOptions,
  applicationRules,
  keyRules,
  loadApplications,
  searchApplications,
  loadKeys,
  loadModelAuthorizations,
  loadUsage,
  selectApplication,
  backToApplicationList,
  changeTab,
  refreshCurrentView,
  changeUsagePage,
  openApplicationForm,
  closeApplicationForm,
  saveApplication,
  disableSelectedApplication,
  enableSelectedApplication,
  deleteSelectedApplication,
  openKeyForm,
  closeKeyForm,
  handleKeyFormClosed,
  saveKey,
  handleKeyAction,
  clearIssuedKey,
  copyIssuedKey,
  openDocs: openApiDocs,
  setKeyScopes,
  setModelIds,
  setLimit,
  setExpiry,
  closeDocs: closeApiDocs
} = useApplications()
</script>

<style scoped lang="scss">
.scope-choices {
  display: grid;
  gap: 10px;
}
.scope-choices :deep(.ant-checkbox-wrapper) {
  margin-inline-start: 0;
  align-items: flex-start;
}
.scope-choices small {
  display: block;
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.developer-api-page {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
  color: var(--color-text);
  background: var(--bg-page);
  font-family: var(--workspace-font-family);
}
.developer-toolbar {
  padding: 26px 28px 20px;
}
.developer-toolbar :deep(.workspace-toolbar__title) {
  font-size: 24px;
  font-weight: 600;
}
.developer-toolbar :deep(.workspace-toolbar__subtitle) {
  margin-top: 6px;
  font-size: 13px;
}
.developer-toolbar :deep(.ant-btn-primary) {
  border-radius: 10px;
}
.developer-api-page :deep(.workspace-status.is-label) {
  background: var(--bg-container);
}

.developer-grid {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  padding: 20px 24px 28px;
}

.developer-grid.is-list {
  container-type: inline-size;
  gap: 0;
  padding: 0 28px 28px;
}

.developer-grid.is-detail {
  overflow: hidden;
}

.application-list-view {
  min-height: 0;
}

.application-tools {
  display: flex;
  flex: 0 0 auto;
  min-height: 40px;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}

.application-tools :deep(.ant-input-affix-wrapper) {
  width: min(360px, 42%);
}
.application-total {
  margin-left: auto;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.application-list-view :deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin-top: 24px;
}

.application-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px 28px;
  align-content: start;
}

@container (min-width: 1360px) {
  .application-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container (max-width: 960px) {
  .application-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (max-width: 560px) {
  .application-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .application-tools :deep(.ant-input-affix-wrapper) {
    width: 100%;
  }
}

.application-row {
  display: flex;
  min-width: 0;
  min-height: 88px;
  align-items: center;
  justify-content: flex-start;
  gap: 14px;
  padding: 10px 12px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.application-row:hover {
  background: var(--gray-25);
}

.application-row__main,
.detail-identity > div {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.application-row__main {
  flex: 1;
  gap: 6px;
}

.application-row__main strong {
  font-size: 15px;
  font-weight: 500;
  line-height: 22px;
}

.application-row__status {
  display: inline-flex;
  flex-shrink: 0;
}

.application-row__main strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.application-detail {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  background: transparent;
}

.application-context-bar {
  flex-wrap: wrap;
  background: transparent;
}

.application-back {
  order: -1;
  margin-right: 4px;
  color: var(--color-text-secondary);
}

.application-back:hover {
  color: var(--color-text);
  background: var(--gray-100);
}

.application-detail > :deep(.overview-panel),
.application-detail > :deep(.keys-panel),
.application-detail > :deep(.models-panel),
.application-detail > :deep(.resources-panel),
.application-detail > :deep(.usage-panel) {
  min-height: 0;
  flex: 0 1 auto;
  overflow: auto;
  scrollbar-gutter: stable;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--bg-container);
}

.application-context-bar,
.detail-identity,
.detail-actions {
  display: flex;
  align-items: center;
}

.application-context-bar {
  flex: 0 0 auto;
  min-height: 76px;
  justify-content: space-between;
  gap: 16px;
  padding: 0 0 18px;
  border-bottom: 0;
}

.detail-identity,
.detail-actions {
  gap: 12px;
}
.detail-identity {
  min-width: 0;
  flex: 1;
}
.detail-identity > :deep(.workspace-status) {
  flex-shrink: 0;
}
.detail-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.detail-identity h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.detail-identity code,
.issued-key code {
  font-family: var(--workspace-font-mono);
}

.detail-tabs {
  flex: 0 0 auto;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--gray-150);
}

.detail-tabs :deep(.ant-tabs-nav) {
  margin: 0;
}

.detail-tabs :deep(.ant-tabs-tab) {
  padding: 13px 4px;
  color: var(--color-text-secondary);
}

.detail-tabs :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) {
  color: var(--main-700);
  font-weight: 600;
}

.stale-notice {
  margin-bottom: 16px;
  padding: 8px 14px;
  color: var(--color-warning-900);
  background: var(--color-warning-50);
  font-size: 12px;
}

.quota-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.issued-key {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}

.issued-key code {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (min-width: 992px) {
  .developer-grid.is-detail {
    padding: 0 var(--workspace-space-6) var(--workspace-space-6);
  }
}

@media (max-width: 640px) {
  .developer-toolbar {
    padding: 20px 16px 16px;
  }
  .developer-grid,
  .developer-grid.is-list {
    padding: 0 16px 16px;
  }
  .application-context-bar {
    gap: 12px;
    padding: 0 0 14px;
  }
  .detail-identity {
    min-width: 0;
    flex: 1 1 100%;
  }
  .detail-actions {
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
  }
  .detail-tabs :deep(.ant-tabs-tab) {
    margin: 0;
    padding: 11px 2px;
    font-size: 12px;
  }
  .detail-tabs :deep(.ant-tabs-tab + .ant-tabs-tab) {
    margin-left: 8px;
  }
  .detail-tabs {
    margin-bottom: 16px;
  }
  .application-list-view :deep(.ant-pagination) {
    flex-wrap: wrap;
    gap: 6px;
  }
  .quota-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
.application-icon {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  place-items: center;
  border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, currentColor 5%, white);
  color: #8054e8;
  font-size: 23px;
}
.application-row:focus-visible {
  outline: 2px solid var(--main-400);
  outline-offset: 2px;
}
.application-back {
  padding-left: 0;
}
.detail-identity {
  margin-right: auto;
}
</style>
