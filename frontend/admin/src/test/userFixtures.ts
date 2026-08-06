import type { Page } from '@/lib/api/pagination'
import type { User } from '@/lib/users/types'

export function buildUserRecord(overrides: Partial<User> = {}): User {
  return {
    id: 'u-1',
    name: 'Budi Santoso',
    email: 'budi@evdekimi.test',
    role: 'client',
    status: 'active',
    created_at: '2026-01-15T02:00:00Z',
    ...overrides,
  }
}

export function buildUserPage(results: User[], overrides: Partial<Page<User>> = {}): Page<User> {
  return {
    results,
    page: 1,
    page_size: 20,
    total: results.length,
    total_pages: 1,
    ...overrides,
  }
}
