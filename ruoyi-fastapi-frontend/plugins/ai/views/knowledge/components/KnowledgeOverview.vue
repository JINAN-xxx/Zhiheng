<template>
  <section class="knowledge-overview">
    <WorkspaceToolbar title="知识库">
      <template #actions>
        <a-tooltip title="刷新知识库">
          <a-button shape="circle" :loading="loading" aria-label="刷新知识库" @click="emit('refresh')">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button v-if="can('add')" type="primary" @click="emit('create')">
          <template #icon><PlusOutlined /></template>
          创建知识库
        </a-button>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-overview__content">
      <WorkspaceState v-if="!can('list')" kind="forbidden" title="暂无知识库管理权限" />
      <template v-else>
        <div class="knowledge-controls">
          <a-input v-model:value="search" allow-clear placeholder="搜索知识库名称或描述" aria-label="搜索知识库">
            <template #prefix><SearchOutlined /></template>
          </a-input>
          <span class="knowledge-total">共 {{ filteredDatabases.length }} 个</span>
        </div>

        <WorkspaceState v-if="loading && !databases.length" kind="loading" />
        <WorkspaceState v-else-if="error && !databases.length" kind="error" :message="error" @retry="emit('refresh')" />
        <WorkspaceState
          v-else-if="!filteredDatabases.length"
          kind="empty"
          :title="search ? '没有匹配的知识库' : '还没有知识库'"
          :message="search ? '调整搜索内容后重试' : '创建知识库后即可上传文档并建立索引'"
        />
        <template v-else>
          <a-alert v-if="error" class="knowledge-alert" type="warning" :message="error" show-icon />
          <div class="knowledge-grid">
            <article v-for="database in filteredDatabases" :key="database.db_id" class="knowledge-card">
              <div class="knowledge-card__header">
                <span class="knowledge-card__icon"><DatabaseOutlined /></span>
                <strong :title="database.name">{{ database.name }}</strong>
                <a-tag>{{ typeLabel(database.kb_type) }}</a-tag>
              </div>
              <p>{{ database.description || '暂无描述' }}</p>
              <div class="knowledge-card__metadata">
                <span v-if="database.enable_knowledge_graph"><ApartmentOutlined /> 已启用知识图谱</span>
                <span v-else><FileTextOutlined /> 向量知识库</span>
                <span>{{ formatTime(database.created_at) }}</span>
              </div>
              <div class="knowledge-card__footer">
                <a-button v-if="can('query')" type="link" size="small" @click="open(database, 'documents')">
                  <FileTextOutlined />管理文档
                </a-button>
                <a-button v-if="can('query')" type="link" size="small" @click="open(database, 'query')">
                  <SearchOutlined />测试检索
                </a-button>
                <a-button
                  v-if="can('query') && database.enable_knowledge_graph"
                  type="link"
                  size="small"
                  @click="open(database, 'graph')"
                >
                  <ApartmentOutlined />查看图谱
                </a-button>
                <a-button v-if="can('edit')" type="link" size="small" @click="emit('edit', database)">
                  <SettingOutlined />编辑知识库
                </a-button>
                <a-button v-if="can('remove')" type="link" size="small" danger @click="emit('remove', database)">
                  <DeleteOutlined />删除知识库
                </a-button>
              </div>
            </article>
          </div>
        </template>
      </template>
    </div>
  </section>
</template>

<script setup>
import {
  ApartmentOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue'
import { computed, ref } from 'vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'

const props = defineProps({
  databases: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  permissions: { type: Array, default: () => [] },
})

const emit = defineEmits(['refresh', 'create', 'edit', 'remove', 'open'])
const search = ref('')
const can = action => props.permissions.includes('*:*:*') || props.permissions.includes('ai:knowledge:' + action)
const typeLabel = type => ({ chroma: 'Chroma', milvus: 'Milvus', lightrag: 'LightRAG' })[type] || type || '知识库'

const filteredDatabases = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return props.databases.filter(database => {
    return !keyword || (database.name + ' ' + database.description).toLowerCase().includes(keyword)
  })
})

function formatTime(value) {
  if (!value) return '暂无更新时间'
  const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? '暂无更新时间' : date.toLocaleString('zh-CN', { hour12: false })
}

function open(database, view) {
  emit('open', { database, view })
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
  min-height: 54px;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
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
  gap: 14px;
  margin-top: 18px;
}

.knowledge-card {
  display: flex;
  min-width: 0;
  min-height: 190px;
  flex-direction: column;
  padding: 17px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
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

.knowledge-card__header strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-text);
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-card__header :deep(.ant-tag) {
  margin: 0;
}

.knowledge-card__icon {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  place-items: center;
  border-radius: 7px;
  color: var(--main-700);
  background: var(--main-50);
  font-size: 17px;
}

.knowledge-card > p {
  display: -webkit-box;
  min-height: 42px;
  margin: 14px 0 10px;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 13px;
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

.knowledge-card__footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 4px;
  margin-top: auto;
  padding-top: 10px;
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
