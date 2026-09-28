let cleanup = null

export function registerWorkTaskSessionCleanup(callback) {
  cleanup = typeof callback === 'function' ? callback : null
  return () => {
    if (cleanup === callback) cleanup = null
  }
}

export function clearWorkTaskSession() {
  cleanup?.()
}
