import assert from 'node:assert/strict'

import {
  FIXED_PRIMARY_COLOR,
  applySettingChange,
  createLayoutSettingPayload,
  parseLayoutSetting,
} from '../../src/design/themeSettings.js'

const restored = parseLayoutSetting(JSON.stringify({
  theme: '#BE123C',
  fixedHeader: true,
  dynamicTitle: false,
  tagsViewPersist: true,
  tagsIcon: false,
  tagsViewStyle: 'card',
  sideTheme: 'theme-dark',
  sidebarLogo: false,
  footerVisible: false,
  unexpected: 'discard',
}))

assert.deepEqual(restored, {
  fixedHeader: true,
  dynamicTitle: false,
  tagsViewPersist: true,
  tagsIcon: false,
  tagsViewStyle: 'card',
  sideTheme: 'theme-dark',
  sidebarLogo: false,
  footerVisible: false,
})
assert.deepEqual(parseLayoutSetting('{invalid'), {})
assert.deepEqual(parseLayoutSetting(''), {})

const state = {
  theme: FIXED_PRIMARY_COLOR,
  fixedHeader: false,
  dynamicTitle: false,
}
assert.deepEqual(applySettingChange(state, { key: 'theme', value: '#BE123C' }), state)
assert.deepEqual(applySettingChange(state, { key: 'fixedHeader', value: true }), {
  ...state,
  fixedHeader: true,
})
assert.deepEqual(applySettingChange(state, { key: 'unknown', value: true }), state)

const payloadSource = {
  theme: '#BE123C',
  sideTheme: 'theme-dark',
  tagsViewPersist: true,
  tagsIcon: false,
  tagsViewStyle: 'card',
  fixedHeader: true,
  sidebarLogo: false,
  dynamicTitle: false,
  footerVisible: false,
  unexpected: 'discard',
}
const payload = createLayoutSettingPayload(payloadSource)
assert.deepEqual(payload, {
  sideTheme: 'theme-dark',
  tagsViewPersist: true,
  tagsIcon: false,
  tagsViewStyle: 'card',
  fixedHeader: true,
  sidebarLogo: false,
  dynamicTitle: false,
  footerVisible: false,
})
assert.ok(!Object.hasOwn(payload, 'theme'))
assert.ok(!Object.hasOwn(payload, 'unexpected'))
assert.deepEqual(parseLayoutSetting(JSON.stringify(payload)), payload)
