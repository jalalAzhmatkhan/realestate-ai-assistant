import { useEffect, useState, type FormEvent } from 'react'

import Button from '@/components/ui/Button'
import { emptyFilters, hasActiveFilters, type PropertyListFilters } from '@/lib/properties/filters'
import {
  PROPERTY_STATUS_LABELS,
  PROPERTY_STATUSES,
  PROPERTY_TYPE_LABELS,
  PROPERTY_TYPES,
  type PropertyStatus,
  type PropertyType,
} from '@/lib/properties/types'

export interface PropertyFiltersProps {
  filters: PropertyListFilters
  /** Always receives the whole filter set; the page resets to 1 on the caller's side. */
  onChange: (filters: PropertyListFilters) => void
}

const INPUT_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-0 ' +
  'focus-visible:outline-blue-600'

/**
 * Text inputs are drafts committed on submit; checkboxes commit immediately, carrying
 * the current draft text with them — so typing a search and then ticking a status
 * applies both, rather than discarding what was typed.
 */
export default function PropertyFilters({ filters, onChange }: PropertyFiltersProps) {
  const [q, setQ] = useState(filters.q)
  const [city, setCity] = useState(filters.city)

  // Re-syncs the drafts when the URL changes from outside this form — the browser Back
  // button, or the "Clear filters" action in the empty state.
  useEffect(() => {
    setQ(filters.q)
    setCity(filters.city)
  }, [filters.q, filters.city])

  function commit(patch: Partial<PropertyListFilters>) {
    onChange({ ...filters, q, city, ...patch })
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    commit({})
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Property filters"
      className="space-y-4 rounded-lg border border-slate-200 bg-white p-4"
    >
      <div className="grid gap-3 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
        <div className="space-y-1">
          <label htmlFor="filter-q" className="block text-sm font-medium text-slate-700">
            Search
          </label>
          <input
            id="filter-q"
            type="search"
            value={q}
            placeholder="Title, description or address"
            onChange={(event) => {
              setQ(event.target.value)
            }}
            className={INPUT_CLASS}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-city" className="block text-sm font-medium text-slate-700">
            City
          </label>
          {/* Free text, not the wireframe's dropdown: no endpoint enumerates the cities
              in use, and the backend matches `city` exactly. See the handover note. */}
          <input
            id="filter-city"
            type="text"
            value={city}
            placeholder="Exact city name"
            onChange={(event) => {
              setCity(event.target.value)
            }}
            className={INPUT_CLASS}
          />
        </div>

        <Button type="submit">Search</Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <CheckboxGroup
          legend="Type"
          name="property_type"
          options={PROPERTY_TYPES}
          labels={PROPERTY_TYPE_LABELS}
          selected={filters.property_type}
          onToggle={(property_type) => {
            commit({ property_type })
          }}
        />
        <CheckboxGroup
          legend="Status"
          name="status"
          options={PROPERTY_STATUSES}
          labels={PROPERTY_STATUS_LABELS}
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

function CheckboxGroup<T extends PropertyType | PropertyStatus>({
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
