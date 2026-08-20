<template>
  <section class="app-main">
    <router-view v-slot="{ Component, route }">
      <AiResourceShell v-if="isAiResourceRoute(route.path) && !route.meta.link">
        <transition name="fade-transform" mode="out-in">
          <keep-alive :include="tagsViewStore.cachedViews">
            <component :is="Component" :key="route.path" />
          </keep-alive>
        </transition>
      </AiResourceShell>
      <transition v-else name="fade-transform" mode="out-in">
        <keep-alive :include="tagsViewStore.cachedViews">
          <component v-if="!route.meta.link" :is="Component" :key="route.path" />
        </keep-alive>
      </transition>
    </router-view>
    <iframe-toggle />
    <copyright />
  </section>
</template>

<script setup>
import copyright from "./Copyright/index"
import iframeToggle from "./IframeToggle/index"
import useTagsViewStore from '@/store/modules/tagsView'
import AiResourceShell from './AiResourceShell.vue'

const route = useRoute()
const tagsViewStore = useTagsViewStore()
const AI_RESOURCE_PATHS = new Set(['/ai/model', '/ai/application', '/ai/usage'])

function isAiResourceRoute(path) {
  return AI_RESOURCE_PATHS.has(path)
}

onMounted(() => {
  addIframe()
})

watchEffect(() => {
  addIframe()
})

function addIframe() {
  if (route.meta.link) {
    useTagsViewStore().addIframeView(route)
  }
}
</script>

<style lang="scss" scoped>
.app-main {
  min-height: 0;
  height: 100%;
  flex: 1;
  width: 100%;
  position: relative;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--workspace-bg);
}

.app-main:has(.copyright) {
  padding-bottom: 36px;
}

@media screen and (max-width: 991px) {
  .app-main {
    padding-bottom: max(60px, calc(constant(safe-area-inset-bottom) + 40px));
    padding-bottom: max(60px, calc(env(safe-area-inset-bottom) + 40px));
    overscroll-behavior-y: none;
  }
}

@supports (-webkit-touch-callout: none) {
  @media screen and (max-width: 991px) {
    .app-main {
      padding-bottom: max(17px, calc(constant(safe-area-inset-bottom) + 10px));
      padding-bottom: max(17px, calc(env(safe-area-inset-bottom) + 10px));
    }
  }
}
</style>

<style lang="scss">
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background-color: #f1f1f1;
}

::-webkit-scrollbar-thumb {
  background-color: #c0c0c0;
  border-radius: 3px;
}
</style>
