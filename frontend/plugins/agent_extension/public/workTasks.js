import useWorkTaskStore from '../stores/workTask.js'

export { useWorkTaskStore }
export { resolveWorkTaskCapabilities } from '../utils/workTask.js'

// The platform sidebar needs task summaries and navigation actions. Execution,
// drafts, member editing and recovery remain owned by the Work module's store.
export function useWorkTaskNavigation(pinia) {
  const store = useWorkTaskStore(pinia)
  return Object.freeze({
    get tasks() { return store.tasks },
    get total() { return store.total },
    get pageNum() { return store.pageNum },
    get pageSize() { return store.pageSize },
    get listQuery() { return store.listQuery },
    get listLoading() { return store.listLoading },
    get listError() { return store.listError },
    loadTasks: (...args) => store.loadTasks(...args),
    renameListedTask: (...args) => store.renameListedTask(...args),
    removeTask: (...args) => store.removeTask(...args),
  })
}
