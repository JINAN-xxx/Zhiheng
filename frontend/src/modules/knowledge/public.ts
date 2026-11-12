import { defineAsyncComponent } from 'vue'
export { knowledgeRoutes } from './routes.ts'
export { projectPreparation } from './preparation.ts'
export { parseHit as parseKnowledgeHit } from './api/index.ts'
export const KnowledgePicker = defineAsyncComponent(
  () => import('./components/KnowledgePicker.vue')
)
export const KnowledgeSources = defineAsyncComponent(
  () => import('./components/KnowledgeSources.vue')
)
export {
  availableBases,
  parseRunAdmission,
  isPreparation,
  getPreparation,
  observePreparation,
  cancelPreparation,
  preparationFinished
} from './api/index.ts'
export type {
  KnowledgeBase,
  KnowledgeHit,
  KnowledgePreparation,
  RunAdmission
} from './api/index.ts'

export { listBases } from './api/index.ts'
export { addChatAttachment } from './api/index.ts'
