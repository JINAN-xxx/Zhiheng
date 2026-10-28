<script setup lang="ts">
import { ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  InputSearch as AInputSearch,
  Pagination as APagination
} from 'ant-design-vue'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspacePanel from '../../../shared/components/WorkspacePanel.vue'
defineProps<{
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
}>()
const emit = defineEmits<{
  refresh: []
  create: []
  search: [value: string]
  page: [value: number]
}>()
const query = ref('')
</script>
<template>
  <section class="admin-page">
    <WorkspaceToolbar :title="title" :subtitle="subtitle || ''">
      <template #actions>
        <AButton :loading="loading" @click="emit('refresh')"
          ><ReloadOutlined aria-hidden="true" />刷新</AButton
        >
        <AButton v-if="canCreate" type="primary" @click="emit('create')"
          ><PlusOutlined aria-hidden="true" />{{ createLabel || '新建' }}</AButton
        >
      </template>
    </WorkspaceToolbar>
    <div class="admin-page__body">
      <AAlert v-if="error" :message="error" type="error" show-icon />
      <WorkspacePanel>
        <div class="admin-tools">
          <AInputSearch
            v-if="!hideSearch"
            v-model:value="query"
            placeholder="搜索名称或编码"
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
      </WorkspacePanel>
    </div>
  </section>
</template>
