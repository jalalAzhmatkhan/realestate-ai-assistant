import type { PageMeta } from '@/lib/api/pagination'
import Button from './Button'

export interface PaginationProps {
  /** Pass the list response straight through — it is already this exact shape. */
  meta: PageMeta
  onPageChange: (page: number) => void
  /** Disables navigation while a page is in flight, so a double click cannot skip a page. */
  isPending?: boolean
}

/** Page buttons rendered around the current page before collapsing to ellipses. */
const WINDOW = 2

export default function Pagination({ meta, onPageChange, isPending = false }: PaginationProps) {
  const { page, page_size: pageSize, total, total_pages: totalPages } = meta

  if (totalPages <= 1) return null

  const first = (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-3"
    >
      <p className="text-sm text-slate-500">
        Showing {first}–{last} of {total}
      </p>

      <div className="flex items-center gap-1">
        <Button
          variant="secondary"
          disabled={isPending || page <= 1}
          onClick={() => {
            onPageChange(page - 1)
          }}
        >
          ← Prev
        </Button>

        {pageWindow(page, totalPages).map((entry, index) =>
          entry === null ? (
            <span key={`gap-${String(index)}`} aria-hidden="true" className="px-1 text-slate-400">
              …
            </span>
          ) : (
            <Button
              key={entry}
              variant={entry === page ? 'primary' : 'ghost'}
              aria-label={`Page ${String(entry)}`}
              aria-current={entry === page ? 'page' : undefined}
              disabled={isPending}
              onClick={() => {
                onPageChange(entry)
              }}
            >
              {entry}
            </Button>
          ),
        )}

        <Button
          variant="secondary"
          disabled={isPending || page >= totalPages}
          onClick={() => {
            onPageChange(page + 1)
          }}
        >
          Next →
        </Button>
      </div>
    </nav>
  )
}

/** Page numbers to render, with `null` marking a collapsed run. */
function pageWindow(page: number, totalPages: number): (number | null)[] {
  const shown = new Set<number>([1, totalPages])
  for (let candidate = page - WINDOW; candidate <= page + WINDOW; candidate += 1) {
    if (candidate >= 1 && candidate <= totalPages) shown.add(candidate)
  }

  const sorted = [...shown].sort((a, b) => a - b)
  const result: (number | null)[] = []
  let previous = 0
  for (const value of sorted) {
    if (previous && value - previous > 1) result.push(null)
    result.push(value)
    previous = value
  }
  return result
}
