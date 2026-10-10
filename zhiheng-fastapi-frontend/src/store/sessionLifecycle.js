// Store ownership is scoped to one application. No business store is imported or
// instantiated here: modules opt in with the name of a synchronous cleanup action.
const owners = new WeakMap()
const cleanups = new WeakMap()

export function sessionLifecyclePlugin({ store, options, pinia }) {
  owners.set(store, pinia)
  let registered = cleanups.get(pinia)
  if (!registered) {
    registered = new Map()
    cleanups.set(pinia, registered)
  }
  if (options.sessionCleanup) {
    registered.set(store, () => store[options.sessionCleanup]())
  }
  const dispose = store.$dispose.bind(store)
  store.$dispose = () => {
    registered.delete(store)
    owners.delete(store)
    dispose()
  }
}

// Failures are reported by store ID only; one module must not prevent the others
// from clearing, or prevent the user module from clearing its authentication.
export function clearSessionState(caller) {
  const registered = cleanups.get(owners.get(caller))
  const failures = []
  for (const [store, cleanup] of [...(registered || [])]) {
    try {
      cleanup()
    } catch {
      failures.push(store.$id)
    }
  }
  return failures
}
