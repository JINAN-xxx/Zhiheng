import type { FileQuery, FileStats, FileUser, ManagedFile } from './api/index.ts'

export interface StorageReadState {
  query: FileQuery
  dates: [string, string] | null
  showSearch: boolean
  files: ManagedFile[]
  stats: FileStats | null
  users: FileUser[]
  usersReady: boolean
  total: number
  hasData: boolean
}

interface StorageReadSnapshot {
  value: StorageReadState
  capturedAt: number
}

export interface StorageReadCache {
  read(): StorageReadSnapshot | undefined
  isFresh(value: StorageReadSnapshot | undefined): boolean
  captureWriter(): (value: StorageReadState, capturedAt: number) => void
  invalidate(): void
  clear(): void
}
