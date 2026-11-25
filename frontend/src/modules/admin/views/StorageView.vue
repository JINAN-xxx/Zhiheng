<script setup lang="ts">
import { inject } from 'vue'
import { FileStorageView } from '../../files/public.ts'
import type { StorageReadCache, StorageReadState } from '../../files/public.ts'
import { adminReadCacheKey } from '../readCache.ts'

const key = 'storage-view'
const adminCache = inject(adminReadCacheKey, null)
const storageCache: StorageReadCache | null = adminCache
  ? {
      read: () => adminCache.getResource<StorageReadState>(key),
      isFresh: (snapshot) => adminCache.isFresh(snapshot),
      captureWriter() {
        const stamp = adminCache.stamp()
        return (value, capturedAt) => adminCache.setResource(key, value, stamp, capturedAt)
      },
      invalidate: () => adminCache.invalidate(),
      clear: () => adminCache.removeResource(key)
    }
  : null
</script>

<template>
  <FileStorageView :read-cache="storageCache" />
</template>
