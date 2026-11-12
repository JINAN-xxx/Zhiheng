import { defineAsyncComponent } from 'vue'

export { parsePoints } from './contracts.ts'
export { creditAccess, formatPoints, monthlyPointsLimit } from './presentation.ts'
export const CreditRatesPanel = defineAsyncComponent(
  () => import('./components/CreditRatesPanel.vue')
)
export const CreditsPanel = defineAsyncComponent(() => import('./components/CreditsPanel.vue'))

export type { CreditState } from './api/index.ts'
export const CreditBillingControl = defineAsyncComponent(
  () => import('./components/CreditBillingControl.vue')
)
