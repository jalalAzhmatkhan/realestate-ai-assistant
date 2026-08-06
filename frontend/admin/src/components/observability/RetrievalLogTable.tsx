import { useState } from 'react'

import { formatSlotTime } from '@/lib/datetime'
import {
  readSort,
  toggleSort,
  type RetrievalLogFilters as Filters,
  type SortableField,
} from '@/lib/observability/retrievalLogFilters'
import type { RetrievalLogItem } from '@/lib/observability/types'
import MetricValue from './MetricValue'

export interface RetrievalLogTableProps {
  logs: RetrievalLogItem[]
  sort: string
  onSortChange: (sort: Filters['sort']) => void
}

const CELL = 'px-3 py-2 text-sm text-slate-700'

/**
 * No `GET /retrieval-logs/{id}` exists by design (`RetrievalLogResponse` docstring) —
 * `results[]` already travels inline on the list item, so a row expands in place rather
 * than navigating anywhere for it.
 */
export default function RetrievalLogTable({ logs, sort, onSortChange }: RetrievalLogTableProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const active = readSort(sort)

  const columns: { key: string; label: string; sortBy?: SortableField }[] = [
    { key: 'created_at', label: 'Time', sortBy: 'created_at' },
    { key: 'query_text', label: 'Query' },
    { key: 'result_count', label: 'Results', sortBy: 'result_count' },
    { key: 'top_score', label: 'Top score', sortBy: 'top_score' },
    { key: 'latency_ms', label: 'Latency' },
    { key: 'toggle', label: '' },
  ]

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[52rem] border-collapse">
        <thead className="border-b border-slate-200 bg-slate-50">
          <tr>
            {columns.map((column) => {
              const sortBy = column.sortBy
              const isActive = sortBy === active.field
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sortBy ? (isActive ? ariaSort(active.direction) : 'none') : undefined}
                  className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-slate-600 uppercase"
                >
                  {sortBy ? (
                    <button
                      type="button"
                      onClick={() => {
                        onSortChange(toggleSort(sort, sortBy))
                      }}
                      className="inline-flex items-center gap-1 rounded uppercase hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                    >
                      {column.label}
                      <span aria-hidden="true" className="text-slate-400">
                        {isActive ? (active.direction === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              )
            })}
          </tr>
        </thead>

        <tbody className="divide-y divide-slate-100">
          {logs.map((log) => {
            const isOpen = expanded.has(log.id)
            return (
              <LogRow key={log.id} log={log} isOpen={isOpen} onToggle={() => toggle(log.id)} />
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function LogRow({
  log,
  isOpen,
  onToggle,
}: {
  log: RetrievalLogItem
  isOpen: boolean
  onToggle: () => void
}) {
  const detailId = `retrieval-log-${log.id}-details`

  return (
    <>
      <tr>
        <td className={`${CELL} whitespace-nowrap`}>{formatSlotTime(log.created_at)}</td>
        <td className={`${CELL} max-w-xs truncate`} title={log.query_text}>
          {log.query_text}
        </td>
        <td className={CELL}>{log.result_count}</td>
        {/* A log with zero results has a `null` top_score — a genuine "no score", not a 0
            (`RetrievalLogResponse.top_score`). */}
        <td className={CELL}>
          <MetricValue value={log.top_score} format={(v) => v.toFixed(3)} />
        </td>
        <td className={CELL}>{log.latency_ms} ms</td>
        <td className={CELL}>
          <button
            type="button"
            aria-expanded={isOpen}
            aria-controls={detailId}
            onClick={onToggle}
            className="text-sm font-medium text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            {isOpen ? 'Hide results' : `Show results (${String(log.result_count)})`}
          </button>
        </td>
      </tr>
      {isOpen ? (
        <tr id={detailId}>
          <td colSpan={columnCount} className="bg-slate-50 px-3 py-3">
            <RetrievedResults log={log} />
          </td>
        </tr>
      ) : null}
    </>
  )
}

const columnCount = 6

function RetrievedResults({ log }: { log: RetrievalLogItem }) {
  if (log.results.length === 0) {
    return <p className="text-sm text-slate-500">No FAQ entries were retrieved for this query.</p>
  }

  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="text-left text-xs font-semibold tracking-wide text-slate-500 uppercase">
          <th scope="col" className="px-2 py-1">
            Rank
          </th>
          <th scope="col" className="px-2 py-1">
            Question
          </th>
          <th scope="col" className="px-2 py-1">
            Category
          </th>
          <th scope="col" className="px-2 py-1">
            Score
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-200">
        {log.results.map((result) => (
          <tr key={`${log.id}-${String(result.rank)}`}>
            <td className="px-2 py-1 text-slate-700">{result.rank}</td>
            <td className="px-2 py-1 text-slate-900">{result.question}</td>
            <td className="px-2 py-1 text-slate-700">{result.category}</td>
            <td className="px-2 py-1 text-slate-700">{result.score.toFixed(3)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ariaSort(direction: 'asc' | 'desc'): 'ascending' | 'descending' {
  return direction === 'asc' ? 'ascending' : 'descending'
}
