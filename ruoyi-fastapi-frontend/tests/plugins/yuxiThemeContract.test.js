import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const light = readFileSync('src/assets/styles/yuxi-tokens.css', 'utf8')
const dark = readFileSync('src/assets/styles/yuxi-tokens.dark.css', 'utf8')
const notice = readFileSync('THIRD_PARTY_NOTICES.md', 'utf8')
const main = readFileSync('src/main.js', 'utf8')

for (const [token, color] of Object.entries({
  '--main-700': '#046a82',
  '--main-500': '#3996ae',
  '--main-50': '#e1f6fb',
  '--gray-50': '#f5f7f7',
  '--gray-150': '#eef0f0',
  '--gray-1000': '#151616',
})) {
  assert.match(light.toLowerCase(), new RegExp(`${token}:\\s*${color}`))
}

assert.match(dark.toLowerCase(), /--main-color:\s*#4a9fb8/)
assert.match(dark.toLowerCase(), /--gray-0:\s*#030303/)
assert.match(dark.toLowerCase(), /--bg-sider:\s*#141414/)
assert.match(notice, /Copyright \(c\) 2025 Yuxi Project Contributors/)
assert.match(notice, /MIT License/)
assert.match(main, /yuxi-tokens\.css/)
assert.match(main, /yuxi-tokens\.dark\.css/)
