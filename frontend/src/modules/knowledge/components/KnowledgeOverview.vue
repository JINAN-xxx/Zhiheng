<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<template>
  <section class="knowledge-overview">
    <WorkspaceToolbar title="知识库" subtitle="管理文档、验证检索、构建知识图谱">
      <template #actions>
        <a-tooltip title="刷新知识库">
          <a-button
            shape="circle"
            :loading="loading"
            aria-label="刷新知识库"
            @click="emit('refresh')"
          >
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button aria-label="创建知识库" type="primary" @click="emit('create')">
          <template #icon><PlusOutlined /></template>
          创建知识库
        </a-button>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-overview__content">
      <div class="knowledge-controls">
        <a-input
          v-model:value="search"
          allow-clear
          placeholder="搜索知识库名称或描述"
          aria-label="搜索知识库"
        >
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <span class="knowledge-total">共 {{ filteredDatabases.length }} 个</span>
      </div>

      <WorkspaceState v-if="loading && !databases.length" kind="loading" />
      <WorkspaceState
        v-else-if="error && !databases.length"
        kind="error"
        :message="error"
        @retry="emit('refresh')"
      />
      <WorkspaceState
        v-else-if="!filteredDatabases.length"
        kind="empty"
        :title="search ? '没有匹配的知识库' : '还没有知识库'"
        :message="search ? '调整搜索内容后重试' : '创建知识库后即可上传文档并建立索引'"
      />
      <template v-else>
        <a-alert v-if="error" class="knowledge-alert" type="warning" :message="error" show-icon />
        <div class="knowledge-grid">
          <article v-for="database in filteredDatabases" :key="database.id" class="knowledge-card">
            <div class="knowledge-card__header">
              <span class="knowledge-card__icon"><DatabaseOutlined /></span>
              <h2 :title="database.name">{{ database.name }}</h2>
              <a-tag>{{ database.access === 'private' ? '私有' : '已登录用户可读' }}</a-tag>
            </div>
            <p>{{ database.description || '暂无描述' }}</p>
            <div class="knowledge-card__metadata">
              <span>{{ database.document_count }} 个文档</span>
              <a-tag :color="database.index_status === 'failed' ? 'error' : 'default'">{{
                indexLabels[database.index_status]
              }}</a-tag>
              <span v-if="graphAvailable && database.enable_knowledge_graph"
                ><ApartmentOutlined /> 已启用知识图谱</span
              >
              <span v-else><FileTextOutlined /> 向量知识库</span>
            </div>
            <div class="knowledge-card__created">
              <span>{{ formatTime(database.created_at) }} · 所有者 {{ database.owner_id }}</span>
            </div>
            <div class="knowledge-card__footer">
              <a-button
                type="link"
                size="small"
                aria-label="管理文档"
                @click="open(database, 'documents')"
              >
                <FileTextOutlined />管理文档
              </a-button>
              <a-button
                type="link"
                size="small"
                aria-label="测试检索"
                @click="open(database, 'query')"
              >
                <SearchOutlined />测试检索
              </a-button>
              <a-button
                type="link"
                size="small"
                :disabled="!graphAvailable"
                aria-label="查看图谱"
                @click="open(database, 'graph')"
              >
                <ApartmentOutlined />查看图谱
              </a-button>
              <a-button
                v-if="database.can_manage"
                type="link"
                size="small"
                @click="emit('acl', database)"
                >访问权限</a-button
              >
              <a-button
                v-if="database.can_manage"
                type="link"
                size="small"
                aria-label="编辑知识库"
                @click="emit('edit', database)"
              >
                <SettingOutlined />编辑知识库
              </a-button>
              <a-button
                v-if="database.can_manage"
                type="link"
                size="small"
                danger
                :disabled="!!database.active_job_id"
                aria-label="删除知识库"
                @click="emit('remove', database)"
              >
                <DeleteOutlined />删除知识库
              </a-button>
            </div>
          </article>
        </div>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import {
  ApartmentOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Tag as ATag,
  Tooltip as ATooltip
} from 'ant-design-vue'
import { computed, ref } from 'vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import type { KnowledgeBase } from '../api/index.ts'
import { formatTime } from '../presentation.ts'
const props = defineProps<{
  databases: KnowledgeBase[]
  loading: boolean
  error: string
  graphAvailable: boolean
}>()
const emit = defineEmits<{
  refresh: []
  create: []
  edit: [base: KnowledgeBase]
  acl: [base: KnowledgeBase]
  remove: [base: KnowledgeBase]
  open: [base: KnowledgeBase, view: string]
}>()
const indexLabels = {
  empty: '尚无索引',
  building: '建立索引中',
  ready: '索引可用',
  failed: '索引失败',
  reindex_required: '需要重建'
}
const search = ref('')
const filteredDatabases = computed(() =>
  props.databases.filter((base) =>
    (base.name + ' ' + base.description).toLowerCase().includes(search.value.trim().toLowerCase())
  )
)
function open(base: KnowledgeBase, view: string): void {
  emit('open', base, view)
}
</script>
<style scoped lang="scss">
.knowledge-overview {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
}

.knowledge-overview__content {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 20px 24px 28px;
}

.knowledge-controls {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: 0;
  background: transparent;
}

.knowledge-controls :deep(.ant-input-affix-wrapper) {
  width: min(360px, 42%);
}

.knowledge-total {
  margin-left: auto;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.knowledge-alert {
  margin-top: 14px;
}

.knowledge-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(230px, 1fr));
  gap: 18px;
  margin-top: 20px;
}

.knowledge-card {
  display: flex;
  min-width: 0;
  min-height: 240px;
  flex-direction: column;
  padding: 22px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
}

.knowledge-card:hover {
  border-color: var(--main-300);
}

.knowledge-card__header {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.knowledge-card__header h2 {
  margin: 0;
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-card__header :deep(.ant-tag) {
  margin: 0;
}

.knowledge-card__icon {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  place-items: center;
  border-radius: 7px;
  color: var(--main-700);
  background: var(--main-50);
  font-size: 17px;
}

.knowledge-card > p {
  display: -webkit-box;
  min-height: 42px;
  margin: 20px 0 14px;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.knowledge-card__metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.knowledge-card__created {
  margin-bottom: 14px;
  color: var(--color-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.knowledge-card__footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 4px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--gray-100);
}

@media (max-width: 1080px) {
  .knowledge-grid {
    grid-template-columns: repeat(2, minmax(230px, 1fr));
  }
}

@media (max-width: 640px) {
  .knowledge-overview__content {
    padding: 16px;
  }

  .knowledge-controls {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .knowledge-controls :deep(.ant-input-affix-wrapper),
  .knowledge-controls :deep(.ant-select) {
    width: 100%;
  }

  .knowledge-total {
    width: 100%;
    margin-left: 0;
    text-align: right;
  }

  .knowledge-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
