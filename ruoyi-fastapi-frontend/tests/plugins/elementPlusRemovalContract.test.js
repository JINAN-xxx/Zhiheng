import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { extname, join, relative } from 'node:path'

const projectRoot = process.cwd()
const sourceRoots = ['src', 'plugins/ai']
const sourceExtensions = new Set(['.js', '.vue'])
const elementPatterns = [
  /from ['"]element-plus['"]/,
  /@element-plus\/icons-vue/,
  /<el-[a-z-]+/,
  /--el-[a-z-]+/,
]

const collectSourceFiles = (directory) => {
  const files = []

  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const entryPath = join(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...collectSourceFiles(entryPath))
    } else if (entry.isFile() && sourceExtensions.has(extname(entry.name))) {
      files.push(entryPath)
    }
  }

  return files
}

export const scanElementPlusReferences = () => sourceRoots
  .flatMap((root) => collectSourceFiles(join(projectRoot, root)))
  .filter((file) => elementPatterns.some((pattern) => pattern.test(readFileSync(file, 'utf8'))))
  .map((file) => relative(projectRoot, file))
  .sort()

const references = scanElementPlusReferences()

assert.ok(references.length > 0, '迁移基线应包含 Element Plus 引用')
assert.ok(references.length <= 109, 'Task 6 后 Element Plus 引用文件不应回升')
assert.ok(references.includes('src/main.js'))
assert.ok(references.includes('plugins/ai/views/chat/index.vue'))
for (const migratedFile of [
  'src/plugins/modal.js',
  'src/plugins/download.js',
  'src/utils/request.js',
  'src/permission.js',
  'src/store/modules/user.js',
]) {
  assert.equal(references.includes(migratedFile), false)
}

console.log(`Element Plus migration baseline: ${references.length} files`)
