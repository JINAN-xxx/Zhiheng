<!-- Adapted from original AiMessage.vue, MIT licensed. -->
<template>
  <div class="ai-message-container" @click="followCitation">
    <div v-if="reasoningContent" class="reasoning-section">
      <button
        type="button"
        class="reasoning-header"
        :aria-expanded="isReasoningExpanded"
        @click="toggleReasoning"
      >
        <RightOutlined class="reasoning-chevron" :class="{ 'is-expanded': isReasoningExpanded }" />
        <span>深度思考过程</span>
        <span v-if="!isThinkingComplete" class="reasoning-status">思考中...</span>
      </button>
      <div v-show="isReasoningExpanded" class="reasoning-content">
        <MarkdownRender
          :content="displayReasoning"
          html-policy="safe"
          :is-dark="false"
          :final="!loading"
          mode="chat"
          code-renderer="shiki"
          :code-block-props="{ monacoOptions: { fontSize: 13, lineHeight: 22 } }"
          @copy="copyMarkdownCode"
        />
      </div>
    </div>
    <div class="ai-message-content message-content">
      <MarkdownRender
        :content="displayContent"
        html-policy="safe"
        :is-dark="false"
        :final="!loading"
        mode="chat"
        code-renderer="shiki"
        :code-block-props="{ monacoOptions: { fontSize: 13, lineHeight: 22 } }"
        @copy="copyMarkdownCode"
      />
    </div>
    <KnowledgeSources v-if="sources.length" ref="sourceList" :sources="sources" />
    <div v-if="loading && !content && !reasoningContent" class="typing-indicator">
      <span></span>
      <span></span>
      <span></span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { copyMarkdownCode } from '../../../shared/markdownClipboard.ts'
import { computed, ref, useId } from 'vue'
import { KnowledgeSources, formatKnowledgeCitations } from '../../knowledge/public.ts'
import type { KnowledgeHit } from '../../knowledge/public.ts'
import { MarkdownRender } from 'markstream-vue'
import { RightOutlined } from '@ant-design/icons-vue'
import { enableKatex, enableMermaid } from 'markstream-vue'
import 'markstream-vue/index.css'
import 'katex/dist/katex.min.css'

enableMermaid()
enableKatex()

const props = withDefaults(
  defineProps<{
    content?: string
    reasoningContent?: string
    loading?: boolean
    sources?: KnowledgeHit[]
  }>(),
  { content: '', reasoningContent: '', loading: false, sources: () => [] }
)
const sourceList = ref<{ showCitation: (number: number) => Promise<void> } | null>(null)
const citationPrefix = `#knowledge-source-${useId().replace(/[^a-z0-9_-]/gi, '')}-`
const displayContent = computed(() =>
  formatKnowledgeCitations(props.content, props.sources, {
    linkPrefix: citationPrefix,
    loading: props.loading
  })
)
const displayReasoning = computed(() =>
  formatKnowledgeCitations(props.reasoningContent, props.sources, {
    linkPrefix: citationPrefix,
    loading: props.loading
  })
)
function followCitation(event: MouseEvent): void {
  const anchor = event.target instanceof Element ? event.target.closest('a') : null
  const href = anchor?.getAttribute('href') ?? ''
  if (!href.startsWith(citationPrefix)) return
  const suffix = href.slice(citationPrefix.length)
  if (!/^[1-9]\d*$/.test(suffix)) return
  event.preventDefault()
  void sourceList.value?.showCitation(Number(suffix))
}

const isReasoningExpanded = ref(false)

const isThinkingComplete = computed(() => {
  return !props.loading || !!props.content
})

function toggleReasoning() {
  isReasoningExpanded.value = !isReasoningExpanded.value
}
</script>

<style lang="scss">
.ai-message-container {
  width: 100%;

  .ai-message-content {
    font-size: var(--workspace-font-size-chat);
    line-height: var(--workspace-line-height-chat);
    color: var(--color-text);
    overflow-wrap: break-word;
    word-break: break-word;
  }

  .markstream-vue {
    --ms-font-sans: var(--workspace-font-family);
    --ms-font-mono: var(--workspace-font-mono);
    --markstream-code-font-family: var(--workspace-font-mono);
    --diffs-font-family: var(--workspace-font-mono);
    --vscode-editor-font-size: var(--workspace-font-size-helper);
    --vscode-editor-line-height: 22px;
    --ms-text-body: var(--workspace-font-size-chat);
    --ms-leading-body: var(--workspace-line-height-chat);
    --ms-flow-paragraph-y: 1em;
  }

  .reasoning-content .markstream-vue {
    --ms-text-body: var(--workspace-font-size-body);
    --ms-leading-body: var(--workspace-line-height);
  }
}

.reasoning-section {
  margin-bottom: 15px;
  border-left: 2px solid var(--gray-150);
  padding-left: 10px;

  .reasoning-header {
    width: 100%;
    display: flex;
    align-items: center;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: pointer;
    color: var(--color-text-secondary);
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
      color: var(--main-color);
      animation: blink 1.5s infinite;
    }
  }

  .reasoning-content {
    font-size: var(--workspace-font-size-body);
    line-height: var(--workspace-line-height);
    color: var(--color-text-secondary);
    padding: 8px 0;
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
    background-color: var(--color-text-secondary);
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
