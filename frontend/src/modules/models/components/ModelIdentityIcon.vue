<template>
  <span class="model-identity-icon" :class="`model-identity-icon--${type}`" aria-hidden="true">
    <component :is="icon" />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ApartmentOutlined, MessageOutlined, SortAscendingOutlined } from '@ant-design/icons-vue'
import type { ChatModel } from '../api/index.ts'

const props = defineProps<{ type: ChatModel['model_type'] }>()
const icon = computed(
  () =>
    ({ chat: MessageOutlined, embedding: ApartmentOutlined, rerank: SortAscendingOutlined })[
      props.type
    ]
)
</script>

<style scoped>
.model-identity-icon {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  place-items: center;
  border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, currentColor 5%, white);
  font-size: 23px;
}
.model-identity-icon--chat {
  color: #4286f5;
}
.model-identity-icon--embedding {
  color: #16acc1;
}
.model-identity-icon--rerank {
  color: #e8a01a;
}
</style>
