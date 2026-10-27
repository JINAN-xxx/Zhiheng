import { defineAsyncComponent } from 'vue'
export { fileRoutes } from './routes.ts'
export { downloadFile, uploadFile, getFile, parseFile as parseManagedFile } from './api/index.ts'
export type { ManagedFile, FileUser } from './api/index.ts'
export const FileAclDrawer = defineAsyncComponent(() => import('./components/FileAclDrawer.vue'))
export { formatFileSize, saveBlob } from './presentation.ts'
