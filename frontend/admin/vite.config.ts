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
    // Every datetime the backend sends is a UTC instant that the app renders in the
    // viewer's own timezone (src/lib/datetime.ts), so any test asserting a rendered time
    // depends on the machine's zone. Pinned to the deployment's own so a developer in
    // another timezone and CI agree — and so a shifted hour shows up as a failure here
    // rather than as a missed viewing.
    env: { TZ: 'Asia/Jakarta' },
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    // A `fetch` stub left standing would silently decide the outcome of every later
    // test in the file.
    unstubGlobals: true,
  },
})
