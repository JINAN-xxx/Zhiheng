<template>
  <ul :id="id" class="api-docs-tree">
    <li v-for="node in nodes" :key="node.id">
      <a
        v-if="node.kind === 'link'"
        :href="`#${node.anchor}`"
        class="api-docs-tree__link"
        :class="{ 'is-active': selectedId === node.anchor }"
        :aria-current="selectedId === node.anchor ? 'location' : undefined"
        @click="$emit('select', node.anchor)"
      >
        <span v-if="node.method" class="api-docs-tree__method">{{ node.method }}</span>
        <span>{{ node.label }}</span>
      </a>
      <details
        v-else
        :open="expandedIds.includes(node.id)"
        @toggle="$emit('toggle', node.id, ($event.target as HTMLDetailsElement).open)"
      >
        <summary
          :aria-expanded="expandedIds.includes(node.id)"
          :aria-controls="`api-docs-children-${node.id}`"
        >
          <RightOutlined class="api-docs-tree__chevron" />
          <span>{{ node.label }}</span>
        </summary>
        <ApiDocsNavigation
          :id="`api-docs-children-${node.id}`"
          :nodes="node.children"
          :selected-id="selectedId"
          :expanded-ids="expandedIds"
          @select="$emit('select', $event)"
          @toggle="(id, open) => $emit('toggle', id, open)"
        />
      </details>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { RightOutlined } from '@ant-design/icons-vue'
import type { ApiDocNavigationNode } from '../apiDocsNavigation.ts'

defineProps<{
  id?: string
  nodes: ApiDocNavigationNode[]
  selectedId: string
  expandedIds: string[]
}>()
defineEmits<{ select: [anchor: string]; toggle: [id: string, open: boolean] }>()
</script>

<style scoped lang="scss">
.api-docs-tree {
  margin: 0;
  padding: 0;
  list-style: none;
}
.api-docs-tree .api-docs-tree {
  padding-left: 12px;
}
.api-docs-tree summary,
.api-docs-tree__link {
  display: flex;
  min-height: 36px;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 5px;
  color: var(--color-text);
  text-decoration: none;
  font-size: var(--workspace-font-size-body);
  line-height: var(--workspace-line-height);
  cursor: pointer;
  overflow-wrap: anywhere;
}
.api-docs-tree summary {
  font-weight: var(--workspace-font-weight-medium);
  list-style: none;
}
.api-docs-tree summary::-webkit-details-marker {
  display: none;
}
.api-docs-tree__chevron {
  flex: 0 0 auto;
  color: var(--color-text-tertiary);
  font-size: var(--workspace-font-size-caption);
}
details[open] > summary .api-docs-tree__chevron {
  transform: rotate(90deg);
}
.api-docs-tree summary:hover,
.api-docs-tree__link:hover {
  background: var(--gray-50);
}
.api-docs-tree__link.is-active {
  background: var(--main-50);
  color: var(--main-700);
}
.api-docs-tree__method {
  min-width: 40px;
  flex: 0 0 auto;
  color: var(--color-text-tertiary);
  font-family: var(--workspace-font-mono);
  font-size: var(--workspace-font-size-caption);
}
.api-docs-tree :focus-visible {
  outline: 2px solid var(--main-500);
  outline-offset: -2px;
}
</style>
