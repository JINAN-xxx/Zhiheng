import 'vue'
declare module 'vue' {
  interface ComponentCustomProps {
    'aria-label'?: string
    'aria-current'?: 'page' | undefined
  }
}
export {}
