<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  InputSearch as AInputSearch,
  Pagination as APagination
} from 'ant-design-vue'
import { PlusOutlined } from '@ant-design/icons-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
const props = defineProps<{
  title: string
  subtitle?: string
  loading: boolean
  error: string
  total: number
  page: number
  canCreate?: boolean
  createLabel?: string
  search?: string
  hideSearch?: boolean
  pageSize?: number
  searchPlaceholder?: string
}>()
const emit = defineEmits<{
  refresh: []
  create: []
  search: [value: string]
  page: [value: number]
}>()
const query = ref(props.search ?? '')
watch(
  () => props.search,
  (value) => {
    query.value = value ?? ''
  }
)
</script>
<template>
  <section class="admin-page">
    <WorkspaceToolbar :title="title" :subtitle="subtitle || ''">
      <template #actions>
        <RefreshButton :label="`刷新${title}`" :loading="loading" @click="emit('refresh')" />
        <AButton v-if="canCreate" type="primary" @click="emit('create')"
          ><PlusOutlined aria-hidden="true" />{{ createLabel || '新建' }}</AButton
        >
      </template>
    </WorkspaceToolbar>
    <div class="admin-page__body">
      <AAlert v-if="error" :message="error" type="error" show-icon />
      <slot name="before-list" />
      <div class="admin-tools">
        <AInputSearch
          v-if="!hideSearch"
          v-model:value="query"
          :placeholder="searchPlaceholder || '搜索名称或编码'"
          aria-label="搜索"
          allow-clear
          @search="emit('search', query)"
        />
        <slot name="filters" />
        <span class="admin-total">共 {{ total }} 条</span>
      </div>
      <slot />
      <APagination
        v-if="total > (pageSize || 20)"
        :current="page"
        :page-size="pageSize || 20"
        :total="total"
        :show-size-changer="false"
        @change="emit('page', $event)"
      />
    </div>
  </section>
</template>

<style scoped>
.admin-page > :deep(.workspace-toolbar) {
  min-height: 72px;
  padding: 12px 32px 16px;
}
@media (max-width: 767px) {
  .admin-page > :deep(.workspace-toolbar) {
    min-height: auto;
    padding: 16px 16px 14px;
  }
}
</style>
