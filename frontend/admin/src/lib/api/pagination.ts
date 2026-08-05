/**
 * The one list envelope shared by `GET /properties`, `GET /bookings` and `GET /users`
 * (backend README, "List endpoints: pagination, filtering, sorting"). The backend
 * implements it once in `app/api/pagination.py` so the three cannot drift; this is the
 * client-side counterpart of that.
 */
export interface PageMeta {
  page: number
  page_size: number
  /** Matching rows after RBAC scoping and filters — not table size. */
  total: number
  total_pages: number
}

export interface Page<T> extends PageMeta {
  results: T[]
}

export interface PageParams {
  page?: number
  page_size?: number
  /** A whitelisted field name, optionally `-`-prefixed for descending. */
  sort?: string
}
