<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { Alert as AAlert, Empty as AEmpty, Modal as AModal, Spin as ASpin } from 'ant-design-vue'
import { getUserPermissions } from '../api/index.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { AdminUser } from '../api/index.ts'
const props = defineProps<{ user: AdminUser | null }>()
const emit = defineEmits<{ close: [] }>()
const items = ref<{ code: string; label: string; group: string; roles: string[] }[]>([])
const loading = ref(false),
  error = ref('')
const scope = new RequestScope()
watch(
  () => props.user?.id,
  async (id) => {
    const ticket = scope.replace()
    items.value = []
    error.value = ''
    if (!id) return
    loading.value = true
    try {
      const value = await getUserPermissions(id, ticket.signal)
      if (ticket.isCurrent()) items.value = value
    } catch (failure) {
      if (ticket.isCurrent() && !isAborted(failure)) error.value = errorMessage(failure)
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <AModal
    :open="!!user"
    title="有效权限与来源角色"
    :footer="null"
    :width="640"
    @cancel="emit('close')"
  >
    <p>{{ user?.display_name || user?.username }}</p>
    <AAlert v-if="!user?.is_active" type="info" message="账号已停用，当前不具有可用权限。" />
    <AAlert v-if="error" type="error" :message="error" show-icon />
    <ASpin :spinning="loading"
      ><div class="user-permissions">
        <section v-for="item in items" :key="item.code">
          <strong>{{
            item.code.endsWith(':enabled')
              ? item.group + '（模块开启）'
              : item.group + ' · ' + item.label
          }}</strong
          ><span>来源：{{ item.roles.join('、') }}</span>
        </section>
        <AEmpty
          v-if="!loading && !items.length && !error"
          description="暂无有效权限"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        /></div
    ></ASpin>
  </AModal>
</template>
<style scoped>
.user-permissions {
  max-height: 60dvh;
  overflow: auto;
}
.user-permissions section {
  display: grid;
  gap: 4px;
  padding: 12px 0;
  border-bottom: 1px solid var(--gray-150);
}
.user-permissions strong {
  font-size: 13px;
  font-weight: 500;
}
.user-permissions span {
  color: var(--color-text-secondary);
  font-size: 12px;
}
</style>
