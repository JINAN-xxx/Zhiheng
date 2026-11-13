import { permissionAliases } from '../../src/shared/permissionAliases.ts'

/** Build an explicitly enabled test identity; production never adds these grants. */
export function grantedPermissions(...codes: string[]): string[] {
  const result = new Set<string>()
  for (const code of codes) {
    let actions = permissionAliases[code] ?? [code]
    if (!actions.length && code.endsWith(':access')) actions = [code.replace(/:access$/, ':read')]
    for (const action of actions) {
      const prefix = action.slice(0, action.lastIndexOf(':'))
      result.add(prefix + ':enabled')
      result.add(prefix + ':read')
      result.add(action)
    }
  }
  return [...result]
}
