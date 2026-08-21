import { computed } from 'vue'
import { theme as antdTheme } from 'ant-design-vue'

import useSettingsStore from '@/store/modules/settings'
import { darkTheme, lightTheme } from './antdTheme'

export function useAntdTheme() {
  const settingsStore = useSettingsStore()

  const themeConfig = computed(() => {
    const baseTheme = settingsStore.isDark ? darkTheme : lightTheme
    return {
      ...baseTheme,
      algorithm: settingsStore.isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: {
        ...baseTheme.token,
        colorPrimary: settingsStore.theme || baseTheme.token.colorPrimary,
      },
    }
  })

  return { themeConfig }
}
