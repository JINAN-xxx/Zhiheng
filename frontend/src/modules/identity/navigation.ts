export function loginDestination(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.includes('\\')
  )
    return '/chat'
  try {
    const url = new URL(value, 'https://local.invalid')
    if (
      url.origin !== 'https://local.invalid' ||
      url.pathname.replace(/\/+$/, '') === '/login' ||
      [...value].some((character) => character.charCodeAt(0) < 32)
    )
      return '/chat'
    return url.pathname + url.search + url.hash
  } catch {
    return '/chat'
  }
}
