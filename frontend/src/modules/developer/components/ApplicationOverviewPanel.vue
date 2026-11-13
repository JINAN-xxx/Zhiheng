<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <section class="overview-panel">
    <div class="overview-grid">
      <div class="overview-field">
        <span>创建时间</span><strong>{{ formatTime(application.createdAt) }}</strong>
      </div>
      <div class="overview-field">
        <span>原始文件额度</span
        ><strong
          >{{ application.sourceFileLimit }} 个 ·
          {{ (application.sourceByteLimit / 1048576).toFixed(0) }} MiB</strong
        >
      </div>
      <div class="overview-field">
        <span>更新时间</span><strong>{{ formatTime(application.updatedAt) }}</strong>
      </div>
    </div>
    <div class="access-guide">
      <strong>开始接入</strong>
      <div class="access-steps">
        <button type="button" aria-label="前往访问密钥" @click="emit('navigate', 'keys')">
          <KeyOutlined />
          <span><strong>创建访问密钥</strong><small>选择可调用的能力、模型与限额</small></span>
          <ArrowRightOutlined />
        </button>
        <button type="button" aria-label="前往资源授权" @click="emit('navigate', 'resources')">
          <SafetyOutlined />
          <span><strong>关联所需资源</strong><small>按需授权文件、知识库与个人技能</small></span>
          <ArrowRightOutlined />
        </button>
        <button type="button" aria-label="前往调用记录" @click="emit('navigate', 'usage')">
          <HistoryOutlined />
          <span><strong>查看调用记录</strong><small>核对请求结果、用量与结算情况</small></span>
          <ArrowRightOutlined />
        </button>
      </div>
      <p>
        资源管理请求独立限速，模型调用按积分规则计费。停用应用后，已有密钥和调用记录会保留，接口调用将被拒绝。
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import {
  ArrowRightOutlined,
  HistoryOutlined,
  KeyOutlined,
  SafetyOutlined
} from '@ant-design/icons-vue'
import type { Application } from '../api/index.ts'
defineProps<{ application: Application }>()
const emit = defineEmits<{ navigate: [tab: 'keys' | 'resources' | 'usage'] }>()
function formatTime(value: string): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '暂无记录'
}
</script>

<style scoped lang="scss">
.overview-panel {
  display: grid;
  gap: 24px;
  padding: 24px;
  container-type: inline-size;
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px 28px;
}

.overview-field {
  display: grid;
  min-height: 44px;
  align-content: center;
  gap: 6px;
  min-width: 0;
}

.overview-field span,
.access-guide p {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.access-guide {
  padding-top: 22px;
  border-top: 1px solid var(--gray-150);
}

.access-guide p {
  margin: 16px 0 0;
  line-height: 1.7;
}
.overview-field strong {
  font-size: 14px;
  font-weight: 400;
  overflow-wrap: anywhere;
}
.access-guide > strong {
  font-size: 14px;
  font-weight: 500;
}
.access-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.access-steps button {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
  padding: 16px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 9px;
  background: var(--bg-container);
  color: var(--main-color);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.access-steps button:hover {
  border-color: var(--main-200);
  background: var(--main-30);
}
.access-steps button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 3px;
}
.access-steps button > span:not(.anticon) {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 5px;
}
.access-steps strong {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
}
.access-steps small {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
@container (max-width: 680px) {
  .overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .access-steps {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 640px) {
  .overview-panel {
    padding: 18px;
  }
  .overview-grid {
    grid-template-columns: 1fr;
    gap: 20px;
  }
}
</style>
