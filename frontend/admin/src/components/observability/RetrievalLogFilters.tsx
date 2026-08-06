import { useEffect, useState, type FormEvent } from 'react'

import Button from '@/components/ui/Button'
import {
  emptyFilters,
  hasActiveFilters,
  type RetrievalLogFilters as Filters,
} from '@/lib/observability/retrievalLogFilters'

export interface RetrievalLogFiltersProps {
  filters: Filters
  /** Always receives the whole filter set; the page resets to 1 on the caller's side. */
  onChange: (filters: Filters) => void
}

const INPUT_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-0 ' +
  'focus-visible:outline-blue-600'

/**
 * `q` is a draft committed on submit, same shape as the users/properties filters.
 * `conversation_id`/`user_id`/the date range are nice-to-have per the task spec, kept as
 * plain text/date inputs rather than lookups — there is no "list conversations" or
 * "list users by id" affordance on this screen to populate a picker from, unlike the
 * bookings filter's property/agent selects.
 */
export default function RetrievalLogFilters({ filters, onChange }: RetrievalLogFiltersProps) {
  const [q, setQ] = useState(filters.q)
  const [conversationId, setConversationId] = useState(filters.conversation_id)
  const [userId, setUserId] = useState(filters.user_id)

  // Re-syncs drafts when the URL changes from outside this form — Back, or "Clear filters".
  useEffect(() => {
    setQ(filters.q)
    setConversationId(filters.conversation_id)
    setUserId(filters.user_id)
  }, [filters.q, filters.conversation_id, filters.user_id])

  function commit(patch: Partial<Filters>) {
    onChange({ ...filters, q, conversation_id: conversationId, user_id: userId, ...patch })
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    commit({})
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Retrieval log filters"
      className="space-y-4 rounded-lg border border-slate-200 bg-white p-4"
    >
      <div className="grid gap-3 sm:grid-cols-[2fr_auto] sm:items-end">
        <div className="space-y-1">
          <label htmlFor="filter-q" className="block text-sm font-medium text-slate-700">
            Search
          </label>
          <input
            id="filter-q"
            type="search"
            value={q}
            placeholder="Query text"
            onChange={(event) => {
              setQ(event.target.value)
            }}
            className={INPUT_CLASS}
          />
        </div>

        <Button type="submit">Search</Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div className="space-y-1">
          <label htmlFor="filter-has-results" className="block text-sm font-medium text-slate-700">
            Results
          </label>
          <select
            id="filter-has-results"
            value={filters.has_results}
            onChange={(event) => {
              onChange({ ...filters, has_results: event.target.value as Filters['has_results'] })
            }}
            className={INPUT_CLASS}
          >
            <option value="">Any</option>
            <option value="true">Had results</option>
            <option value="false">No results</option>
          </select>
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-conversation-id" className="block text-sm font-medium text-slate-700">
            Conversation ID
          </label>
          <input
            id="filter-conversation-id"
            type="text"
            value={conversationId}
            onChange={(event) => {
              setConversationId(event.target.value)
            }}
            onBlur={() => {
              commit({})
            }}
            className={INPUT_CLASS}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-user-id" className="block text-sm font-medium text-slate-700">
            User ID
          </label>
          <input
            id="filter-user-id"
            type="text"
            value={userId}
            onChange={(event) => {
              setUserId(event.target.value)
            }}
            onBlur={() => {
              commit({})
            }}
            className={INPUT_CLASS}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-date-from" className="block text-sm font-medium text-slate-700">
            From
          </label>
          <input
            id="filter-date-from"
            type="date"
            value={filters.date_from}
            onChange={(event) => {
              onChange({ ...filters, date_from: event.target.value })
            }}
            className={INPUT_CLASS}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-date-to" className="block text-sm font-medium text-slate-700">
            To
          </label>
          <input
            id="filter-date-to"
            type="date"
            value={filters.date_to}
            onChange={(event) => {
              onChange({ ...filters, date_to: event.target.value })
            }}
            className={INPUT_CLASS}
          />
        </div>
      </div>

      {hasActiveFilters(filters) ? (
        <Button
          variant="ghost"
          onClick={() => {
            onChange(emptyFilters())
          }}
        >
          Clear filters
        </Button>
      ) : null}
    </form>
  )
}
