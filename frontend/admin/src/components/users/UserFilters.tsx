import { useEffect, useState, type FormEvent } from 'react'

import Button from '@/components/ui/Button'
import { emptyFilters, hasActiveFilters, type UserListFilters } from '@/lib/users/filters'
import {
  USER_ROLE_LABELS,
  USER_ROLES,
  USER_STATUS_LABELS,
  USER_STATUSES,
  type UserRole,
  type UserStatus,
} from '@/lib/users/types'

export interface UserFiltersProps {
  filters: UserListFilters
  /** Always receives the whole filter set; the page resets to 1 on the caller's side. */
  onChange: (filters: UserListFilters) => void
}

const INPUT_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-0 ' +
  'focus-visible:outline-blue-600'

/**
 * Same shape as the property filters: the search box is a draft committed on submit,
 * checkboxes commit immediately and carry the current draft text with them, so ticking
 * `admin` after typing a name applies both rather than discarding what was typed.
 */
export default function UserFilters({ filters, onChange }: UserFiltersProps) {
  const [q, setQ] = useState(filters.q)

  // Re-syncs the draft when the URL changes from outside this form — Back, or the
  // "Clear filters" action in the empty state.
  useEffect(() => {
    setQ(filters.q)
  }, [filters.q])

  function commit(patch: Partial<UserListFilters>) {
    onChange({ ...filters, q, ...patch })
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    commit({})
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="User filters"
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
            placeholder="Name or email"
            onChange={(event) => {
              setQ(event.target.value)
            }}
            className={INPUT_CLASS}
          />
        </div>

        <Button type="submit">Search</Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <CheckboxGroup
          legend="Role"
          name="role"
          options={USER_ROLES}
          labels={USER_ROLE_LABELS}
          selected={filters.role}
          onToggle={(role) => {
            commit({ role })
          }}
        />
        <CheckboxGroup
          legend="Status"
          name="status"
          options={USER_STATUSES}
          labels={USER_STATUS_LABELS}
          selected={filters.status}
          onToggle={(status) => {
            commit({ status })
          }}
        />
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

interface CheckboxGroupProps<T extends string> {
  legend: string
  name: string
  options: readonly T[]
  labels: Record<T, string>
  selected: T[]
  onToggle: (selected: T[]) => void
}

function CheckboxGroup<T extends UserRole | UserStatus>({
  legend,
  name,
  options,
  labels,
  selected,
  onToggle,
}: CheckboxGroupProps<T>) {
  return (
    <fieldset>
      <legend className="text-sm font-medium text-slate-700">{legend}</legend>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
        {options.map((option) => (
          <label key={option} className="flex items-center gap-1.5 text-sm text-slate-700">
            <input
              type="checkbox"
              name={name}
              value={option}
              checked={selected.includes(option)}
              onChange={(event) => {
                onToggle(
                  event.target.checked
                    ? [...selected, option]
                    : selected.filter((value) => value !== option),
                )
              }}
              className="size-4 rounded border-slate-300 text-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            />
            {labels[option]}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
