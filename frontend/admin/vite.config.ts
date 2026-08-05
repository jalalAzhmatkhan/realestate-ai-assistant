import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // strictPort: the backend's CORS_ALLOWED_ORIGINS lists this exact origin, and
    // cookie auth is credentialed, so silently falling back to 5174 would break
    // login with an opaque CORS error rather than an obvious "port in use".
    port: 5173,
    strictPort: true,
  },
  test: {
    // happy-dom, not jsdom: jsdom installs its own AbortSignal while leaving Node's
    // undici-backed fetch/Request in place, and undici rejects a foreign AbortSignal
    // ("Expected signal to be an instance of AbortSignal"). React Router builds a
    // Request on every navigation, so under jsdom any test that navigates throws —
    // a test-environment artifact with no browser equivalent, but one that would
    // block routing/RBAC tests outright. happy-dom ships a self-consistent set.
    environment: 'happy-dom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
})
