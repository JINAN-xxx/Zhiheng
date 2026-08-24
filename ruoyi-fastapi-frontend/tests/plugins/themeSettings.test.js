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

const payload = createLayoutSettingPayload({
  theme: '#BE123C',
  fixedHeader: true,
  dynamicTitle: false,
})
assert.deepEqual(payload, {
  fixedHeader: true,
  dynamicTitle: false,
})
assert.ok(!Object.hasOwn(payload, 'theme'))
