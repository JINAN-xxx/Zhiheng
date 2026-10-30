export interface BackgroundVisibilitySource {
  readonly hidden: boolean
  addEventListener(type: 'visibilitychange', listener: () => void): void
  removeEventListener(type: 'visibilitychange', listener: () => void): void
}

/** Pause background reads in hidden tabs; returning to the tab observes immediately. */
export function waitForBackgroundObservation(
  signal: AbortSignal,
  delay = 1500,
  visibility: BackgroundVisibilitySource | undefined = typeof document === 'undefined'
    ? undefined
    : document
): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | undefined
    function cleanup(): void {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      visibility?.removeEventListener('visibilitychange', changed)
    }
    function finish(): void {
      cleanup()
      resolve()
    }
    function abort(): void {
      cleanup()
      reject(new DOMException('Observation aborted', 'AbortError'))
    }
    function changed(): void {
      clearTimeout(timer)
      if (!visibility?.hidden) finish()
    }
    if (signal.aborted) {
      abort()
      return
    }
    signal.addEventListener('abort', abort, { once: true })
    visibility?.addEventListener('visibilitychange', changed)
    if (!visibility?.hidden) timer = setTimeout(finish, delay)
  })
}
