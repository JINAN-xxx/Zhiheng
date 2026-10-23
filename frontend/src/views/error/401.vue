<template>
  <AuthShell class="auth-shell" wide>
    <a-result
      status="403"
      sub-title="当前账户没有访问此资源的权限，请返回上一页或进入工作台。"
      title="401"
    >
      <template #extra>
        <a-space wrap>
          <a-button @click="back">
            <template #icon><ArrowLeftOutlined /></template>
            返回上一页
          </a-button>
          <a-button type="primary" @click="goHome">进入工作台</a-button>
        </a-space>
      </template>
    </a-result>
  </AuthShell>
</template>

<script setup>
import { ArrowLeftOutlined } from '@ant-design/icons-vue'

import { AuthShell } from '@/components/Antd'

const route = useRoute()
const router = useRouter()

function goHome() {
  router.push('/')
}

function back() {
  if (route.query.noGoBack) {
    goHome()
  } else {
    router.go(-1)
  }
}
</script>

<style scoped>
:deep(.ant-result) {
  padding: 12px 0;
}
</style>
