import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { componentSize, darkTheme, lightTheme } from '../../src/design/antdTheme.js'

const antdRoot = readFileSync('src/design/AntdRoot.vue', 'utf8')
const main = readFileSync('src/main.js', 'utf8')
const themeUtils = readFileSync('src/utils/theme.js', 'utf8')

assert.equal(lightTheme.token.colorPrimary, '#3996AE')
assert.equal(lightTheme.token.borderRadius, 4)
assert.equal(lightTheme.token.borderRadiusLG, 8)
assert.equal(lightTheme.token.colorBgLayout, '#F5F7F7')
assert.equal(darkTheme.token.colorBgLayout, '#0C0D0D')
assert.equal(componentSize, 'middle')

assert.match(antdRoot, /a-config-provider/)
assert.match(antdRoot, /zhCN/)
assert.match(antdRoot, /themeConfig/)
assert.match(antdRoot, /getPopupContainer/)
assert.match(main, /ant-design-vue\/dist\/reset\.css/)
assert.match(main, /app\.use\(Antd\)/)
assert.doesNotMatch(themeUtils, /--el-/)
