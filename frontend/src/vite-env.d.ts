/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_DATA_SOURCE?: 'http' | 'static'
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
