import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceRoot = fileURLToPath(new URL('../src/', import.meta.url))
const parts = (path) => relative(sourceRoot, path).split(sep)

function localTarget(filename, specifier) {
  if (specifier.startsWith('@/')) return resolve(sourceRoot, specifier.slice(2))
  if (specifier.startsWith('~/')) return resolve(sourceRoot, '..', specifier.slice(2))
  if (specifier.startsWith('/src/')) return resolve(sourceRoot, specifier.slice(5))
  return specifier.startsWith('.') ? resolve(dirname(filename), specifier) : null
}

function edgeError(filename, specifier) {
  const target = localTarget(filename, specifier)
  if (target === null) return null
  const source = parts(filename)
  const destination = parts(target)
  if (destination[0] === '..') return '正式源码的本地依赖必须位于 src 中'
  if (!['app', 'shared', 'modules'].includes(destination[0])) return null
  if (source[0] === 'shared' && destination[0] !== 'shared')
    return 'shared 只能依赖通用代码，不能依赖应用或业务模块'
  if (source[0] === 'modules' && destination[0] === 'app') return '业务模块不能反向依赖应用装配层'
  if (
    destination[0] === 'modules' &&
    (source[0] === 'app' || (source[0] === 'modules' && source[1] !== destination[1])) &&
    !(
      destination.length === 3 &&
      ['public.ts', 'routes.ts', 'public', 'routes'].includes(destination[2])
    )
  )
    return '跨业务模块只能使用 public.ts 或 routes.ts 公开入口'
  return null
}

/** @type {import('eslint').Rule.RuleModule} */
export const moduleBoundaries = {
  meta: {
    type: 'problem',
    schema: [],
    messages: { boundary: '{{reason}}' }
  },
  create(context) {
    const filename = context.filename
    const httpBindings = new Set()
    const httpNamespaces = new Set()
    const report = (node, reason) =>
      context.report({ node, messageId: 'boundary', data: { reason } })
    function check(node) {
      if (typeof node.value !== 'string') return
      const reason = edgeError(filename, node.value)
      if (reason) report(node, reason)
    }
    return {
      ImportDeclaration(node) {
        check(node.source)
        const target =
          typeof node.source.value === 'string' ? localTarget(filename, node.source.value) : null
        if (
          target !== null &&
          ['shared/http.ts', 'shared/http'].includes(parts(target).join('/'))
        ) {
          for (const entry of node.specifiers) {
            if (
              entry.type === 'ImportSpecifier' &&
              entry.imported.type === 'Identifier' &&
              entry.imported.name === 'http'
            )
              httpBindings.add(entry.local.name)
            if (entry.type === 'ImportNamespaceSpecifier') httpNamespaces.add(entry.local.name)
          }
        }
      },
      ExportNamedDeclaration(node) {
        if (node.source) check(node.source)
      },
      ExportAllDeclaration(node) {
        check(node.source)
      },
      ImportExpression(node) {
        if (node.source.type === 'Literal') check(node.source)
        else report(node.source, '动态导入路径必须可静态解析，以验证模块边界')
      },
      TSImportType(node) {
        if (node.argument?.type === 'TSLiteralType') check(node.argument.literal)
      },
      CallExpression(node) {
        if (parts(filename)[0] !== 'app' || node.callee.type !== 'MemberExpression') return
        const method = node.callee
        if (
          method.property.type !== 'Identifier' ||
          !['request', 'open'].includes(method.property.name)
        )
          return
        const owner = method.object
        const direct = owner.type === 'Identifier' && httpBindings.has(owner.name)
        const namespace =
          owner.type === 'MemberExpression' &&
          owner.object.type === 'Identifier' &&
          httpNamespaces.has(owner.object.name) &&
          owner.property.type === 'Identifier' &&
          owner.property.name === 'http'
        if (direct || namespace)
          report(node, '业务 HTTP 协议由所属模块 api 维护，应用壳只消费公开查询')
      }
    }
  }
}
