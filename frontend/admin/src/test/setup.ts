import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// RTL only self-registers this when Vitest runs with `globals: true`, which this project
// does not. Without it every render in a file stacks up in the same document and queries
// start reporting "found multiple elements".
afterEach(cleanup)
