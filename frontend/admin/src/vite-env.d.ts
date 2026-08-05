/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  /** Dev-only role stand-in until F3 lands login — see lib/auth/currentUser.ts. */
  readonly VITE_DEV_ROLE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
