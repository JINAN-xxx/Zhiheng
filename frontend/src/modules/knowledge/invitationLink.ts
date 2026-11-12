export function knowledgeInvitationPath(value: string, origin: string): string | null {
  try {
    const url = new URL(value.trim(), origin)
    if (
      url.origin !== origin ||
      url.pathname !== '/knowledge/join' ||
      url.username ||
      url.password ||
      url.search ||
      !/^#[A-Za-z0-9_-]{43}$/.test(url.hash)
    )
      return null
    return url.pathname + url.hash
  } catch {
    return null
  }
}
