<script setup lang="ts">
import { computed } from 'vue'
import { formatFileSize } from '../presentation.ts'
import type { FileStats } from '../api/index.ts'

const props = withDefaults(defineProps<{ stats: FileStats | null; loading?: boolean }>(), {
  loading: false
})
const cards = computed(() => {
  const stats = props.stats
  return [
    {
      key: 'files',
      label: '文件数量',
      value: stats?.totalCount.toLocaleString() ?? '—',
      unit: stats ? '个文件' : '',
      details: stats ? [`正常 ${stats.activeCount}`, `回收站 ${stats.deletedCount}`] : [],
      warning: false
    },
    {
      key: 'space',
      label: '占用空间',
      value: stats ? formatFileSize(stats.totalSize) : '—',
      unit: '',
      details: stats
        ? [
            `公开 ${formatFileSize(stats.publicSize)}`,
            `受保护 ${formatFileSize(stats.privateSize)}`
          ]
        : [],
      warning: false
    },
    {
      key: 'expiry',
      label: '已过期文件',
      value: stats?.expiredCount.toLocaleString() ?? '—',
      unit: stats ? '个文件' : '',
      details: stats ? [`7 天内到期 ${stats.retentionExpiringCount}`] : [],
      warning: !!stats?.expiredCount
    },
    {
      key: 'grants',
      label: '临期授权',
      value: stats?.aclExpiringCount.toLocaleString() ?? '—',
      unit: stats ? '项' : '',
      details: stats ? ['7 天内到期的授权项'] : [],
      warning: !!stats?.aclExpiringCount
    }
  ]
})
</script>

<template>
  <section class="file-statistics" role="region" aria-label="当前查询概览" :aria-busy="loading">
    <header class="file-statistics-header">
      <h2>文件概览</h2>
      <span role="status">{{ loading ? '正在更新…' : '随当前筛选条件更新' }}</span>
    </header>
    <div class="file-statistics-content">
      <article
        v-for="card in cards"
        :key="card.key"
        class="file-stat-item"
        :class="{ 'has-warning': card.warning }"
        role="group"
        :aria-label="card.label"
      >
        <span class="file-stat-label">{{ card.label }}</span>
        <strong class="file-stat-value" :title="card.value">
          {{ card.value }}<span v-if="card.unit" class="file-stat-unit">{{ ' ' + card.unit }}</span>
        </strong>
        <div class="file-stat-extra">
          <span v-for="detail in card.details" :key="detail">{{ detail }}</span>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.file-statistics {
  margin-bottom: 20px;
}
.file-statistics-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.file-statistics-header h2 {
  margin: 0;
  color: var(--workspace-text);
  font-size: var(--workspace-font-size-body);
  font-weight: var(--workspace-font-weight-medium);
}
.file-statistics-header > span {
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
}
.file-statistics-content {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.file-stat-item {
  min-width: 0;
  padding: 12px 16px;
  background: var(--workspace-surface);
  border: 1px solid var(--workspace-border);
  border-radius: var(--workspace-radius-md);
}
.file-stat-label {
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
}
.file-stat-value {
  display: block;
  min-width: 0;
  margin-top: 3px;
  overflow: hidden;
  color: var(--workspace-text);
  font-size: var(--workspace-font-size-title);
  font-weight: var(--workspace-font-weight-medium);
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-stat-unit {
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
  font-weight: var(--workspace-font-weight-regular);
}
.file-stat-extra {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  min-height: 18px;
  margin-top: 5px;
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
  line-height: 18px;
}
.has-warning .file-stat-value {
  color: var(--workspace-warning);
}
@media (max-width: 1100px) {
  .file-statistics-content {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 560px) {
  .file-statistics-content {
    gap: 10px;
  }
  .file-stat-item {
    padding: 10px 12px;
  }
  .file-statistics-header > span,
  .file-stat-extra {
    font-size: 11px;
  }
}
</style>
