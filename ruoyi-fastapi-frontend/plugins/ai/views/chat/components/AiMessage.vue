<template>
  <div class="ai-message-container">
    <div v-if="reasoningContent" class="reasoning-section">
      <button type="button" class="reasoning-header" :aria-expanded="isReasoningExpanded" @click="toggleReasoning">
        <RightOutlined class="reasoning-chevron" :class="{ 'is-expanded': isReasoningExpanded }" />
        <span>深度思考过程</span>
        <span class="reasoning-status" v-if="!isThinkingComplete"
          >思考中...</span
        >
      </button>
      <div v-show="isReasoningExpanded" class="reasoning-content">
        <MarkdownRender
          :content="reasoningContent"
          :is-dark="isDark"
          :final="!loading"
          mode="chat"
          code-renderer="shiki"
        />
      </div>
    </div>
    <div class="ai-message-content">
      <MarkdownRender
        :content="content"
        :is-dark="isDark"
        :final="!loading"
        mode="chat"
        code-renderer="shiki"
      />
    </div>
    <div
      v-if="loading && !content && !reasoningContent"
      class="typing-indicator"
    >
      <span></span>
      <span></span>
      <span></span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { MarkdownRender } from "markstream-vue";
import { useDark } from "@vueuse/core";
import { RightOutlined } from '@ant-design/icons-vue'
import { enableKatex, enableMermaid } from "markstream-vue";
import "markstream-vue/index.css";
import "katex/dist/katex.min.css";

enableMermaid();
enableKatex();

const isDark = useDark();

const props = defineProps({
  content: {
    type: String,
    default: "",
  },
  reasoningContent: {
    type: String,
    default: "",
  },
  loading: {
    type: Boolean,
    default: false,
  },
});

const isReasoningExpanded = ref(true);

const isThinkingComplete = computed(() => {
  return !!props.content;
});

function toggleReasoning() {
  isReasoningExpanded.value = !isReasoningExpanded.value;
}
</script>

<style lang="scss">
.ai-message-container {
  width: 100%;

  .ai-message-content {
    font-size: 14px;
    line-height: 1.6;
    color: var(--workspace-text);
    overflow-wrap: break-word;
    word-break: break-word;
  }
}

.reasoning-section {
  margin-bottom: 15px;
  border-left: 2px solid var(--workspace-border);
  padding-left: 10px;

  .reasoning-header {
    width: 100%;
    display: flex;
    align-items: center;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: pointer;
    color: var(--workspace-text-secondary);
    font: inherit;
    font-size: 13px;
    text-align: left;
    user-select: none;
    margin-bottom: 5px;

    .reasoning-chevron {
      margin-right: 4px;
      transition: transform 0.3s;
      &.is-expanded {
        transform: rotate(90deg);
      }
    }

    .reasoning-status {
      margin-left: 10px;
      font-size: 12px;
      color: #e6a23c;
      animation: blink 1.5s infinite;
    }
  }

  .reasoning-content {
    font-size: 13px;
    color: var(--workspace-text-secondary);
    padding: 8px;
    background-color: var(--workspace-surface-muted);
    border-radius: var(--workspace-radius-sm);
    overflow-wrap: break-word;
    word-break: break-word;
  }
}

@keyframes blink {
  0% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
  100% {
    opacity: 1;
  }
}

.typing-indicator {
  display: flex;
  align-items: center;
  padding: 8px 0;

  span {
    display: inline-block;
    width: 6px;
    height: 6px;
    background-color: var(--workspace-text-secondary);
    border-radius: 50%;
    animation: typing 1.4s infinite ease-in-out both;
    margin-right: 4px;

    &:nth-child(1) {
      animation-delay: -0.32s;
    }

    &:nth-child(2) {
      animation-delay: -0.16s;
    }
  }
}

@keyframes typing {
  0%,
  80%,
  100% {
    transform: scale(0);
  }
  40% {
    transform: scale(1);
  }
}
</style>
