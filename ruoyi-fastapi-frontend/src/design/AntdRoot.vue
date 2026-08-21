<template>
  <a-config-provider
    :component-size="componentSize"
    :get-popup-container="getPopupContainer"
    :locale="zhCN"
    :theme="themeConfig"
  >
    <a-app>
      <AntdFeedbackHost>
        <div class="antd-app-root">
          <router-view />
        </div>
      </AntdFeedbackHost>
    </a-app>
  </a-config-provider>
</template>

<script setup>
import zhCN from 'ant-design-vue/es/locale/zh_CN'

import AntdFeedbackHost from './AntdFeedbackHost.vue'
import { componentSize } from './antdTheme'
import { useAntdTheme } from './useAntdTheme'

const { themeConfig } = useAntdTheme()

function getPopupContainer(triggerNode) {
  return triggerNode?.parentElement ?? document.body
}
</script>
