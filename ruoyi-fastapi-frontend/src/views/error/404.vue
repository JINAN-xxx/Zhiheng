<template>
  <AuthShell class="auth-shell" wide>
    <a-result
      status="404"
      sub-title="您访问的页面不存在、已移动，或当前路由尚未加载。"
      title="找不到页面"
    >
      <template #extra>
        <a-space wrap>
          <a-button @click="router.go(-1)">
            <template #icon><ArrowLeftOutlined /></template>
            返回上一页
          </a-button>
          <a-button type="primary" @click="router.push('/')">进入工作台</a-button>
        </a-space>
      </template>
    </a-result>
  </AuthShell>
</template>

<script setup>
import { ArrowLeftOutlined } from '@ant-design/icons-vue'

import { AuthShell } from '@/components/Antd'

const router = useRouter()
</script>

<style scoped>
:deep(.ant-result) {
  padding: 12px 0;
}
</style>
