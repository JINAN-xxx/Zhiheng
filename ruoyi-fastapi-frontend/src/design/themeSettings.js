export const FIXED_PRIMARY_COLOR = '#046A82'

const persistedLayoutKeys = [
  'sideTheme',
  'tagsViewPersist',
  'tagsIcon',
  'tagsViewStyle',
  'fixedHeader',
  'sidebarLogo',
  'dynamicTitle',
  'footerVisible',
]

export function parseLayoutSetting(serialized) {
  if (!serialized) return {}

  try {
    const parsed = JSON.parse(serialized)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}

    return persistedLayoutKeys.reduce((settings, key) => {
      if (Object.hasOwn(parsed, key)) settings[key] = parsed[key]
      return settings
    }, {})
  } catch {
    return {}
  }
}

export function createLayoutSettingPayload(settings) {
  return {
    fixedHeader: settings.fixedHeader,
    dynamicTitle: settings.dynamicTitle,
  }
}

export function applySettingChange(state, { key, value }) {
  if (key === 'theme' || !Object.hasOwn(state, key)) return { ...state }

  return {
    ...state,
    [key]: value,
  }
}
