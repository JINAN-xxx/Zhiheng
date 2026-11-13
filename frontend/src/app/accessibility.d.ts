import 'vue'
import type { AriaAttributes } from 'vue'
declare module 'vue' {
  interface ComponentCustomProps {
    'aria-label'?: string
    'aria-required'?: AriaAttributes['aria-required']
    'aria-expanded'?: AriaAttributes['aria-expanded']
    'aria-pressed'?: AriaAttributes['aria-pressed']
    'aria-current'?: 'page' | undefined
  }
}
export {}
