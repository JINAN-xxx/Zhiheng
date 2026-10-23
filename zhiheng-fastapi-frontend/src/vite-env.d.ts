/// <reference types="vite/client" />

interface ViteTypeOptions {
  strictImportMetaEnv: unknown
}

interface ImportMetaEnv {
  readonly VITE_APP_TITLE: string
  readonly VITE_APP_ENV: 'development' | 'production' | 'staging' | 'docker'
  readonly VITE_APP_BASE_API: string
  readonly VITE_BUILD_COMPRESS?: string
}
