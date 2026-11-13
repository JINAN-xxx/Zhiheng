<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { identity } from '../../modules/identity/public.ts'
import { platformModules, hasModule } from '../../shared/platformAccess.ts'
import WorkspacePage from '../../shared/components/WorkspacePage.vue'
const route = useRoute()
const available = computed(() =>
  platformModules.filter((module) => hasModule(identity.value, module.id))
)
</script>
<template>
  <WorkspacePage title="功能访问">
    <section class="access-state" role="status">
      <h2>{{ route.query.denied ? '当前账号未获准使用此模块' : '暂未分配功能权限' }}</h2>
      <p>请联系管理员在角色权限中开启所需模块。</p>
      <nav v-if="available.length" aria-label="可用功能">
        <RouterLink v-for="item in available" :key="item.id" :to="item.path">{{
          item.label
        }}</RouterLink>
      </nav>
      <RouterLink to="/profile">个人资料</RouterLink>
    </section>
  </WorkspacePage>
</template>
<style scoped>
.access-state {
  padding: 60px 24px;
  text-align: center;
}
.access-state h2 {
  font-size: 20px;
  font-weight: 500;
}
.access-state p {
  color: var(--color-text-secondary);
}
.access-state nav {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 16px;
  margin: 24px 0;
}
</style>
