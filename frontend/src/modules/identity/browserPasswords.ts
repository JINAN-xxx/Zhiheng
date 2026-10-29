interface PasswordSaveBrowser {
  readonly isSecureContext: boolean
  readonly top: unknown
  readonly self: unknown
  readonly PasswordCredential?: new (input: { id: string; password: string }) => Credential
  readonly navigator: {
    readonly credentials?: { store(credential: Credential): Promise<unknown> }
  }
}

export type PasswordSaveResult = 'requested' | 'unsupported' | 'failed'

export async function savePasswordInBrowser(
  browser: PasswordSaveBrowser,
  username: string,
  password: string
): Promise<PasswordSaveResult> {
  if (
    !browser.isSecureContext ||
    browser.top !== browser.self ||
    typeof browser.PasswordCredential !== 'function' ||
    typeof browser.navigator.credentials?.store !== 'function'
  )
    return 'unsupported'
  try {
    const credential = new browser.PasswordCredential({ id: username, password })
    await browser.navigator.credentials.store(credential)
    return 'requested'
  } catch {
    return 'failed'
  }
}
