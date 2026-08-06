import { useQuery } from '@tanstack/react-query'

import Button from '@/components/ui/Button'
import { useCurrentUser } from '@/lib/auth/useCurrentUser'
import { agentOptionsQueryOptions, propertyOptionsQueryOptions } from '@/lib/bookings/filterOptions'
import { emptyFilters, hasActiveFilters, type BookingListFilters } from '@/lib/bookings/filters'
import { BOOKING_STATUS_LABELS, BOOKING_STATUSES, type BookingStatus } from '@/lib/bookings/types'

export interface BookingFiltersProps {
  filters: BookingListFilters
  /** Always receives the whole filter set; the page resets to 1 on the caller's side. */
  onChange: (filters: BookingListFilters) => void
}

const CONTROL_CLASS =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 ' +
  'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-blue-600 ' +
  'disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500'

/**
 * Every control commits on change — there is no free-text field here to draft, so the
 * submit-on-enter dance the property filters need does not apply.
 */
export default function BookingFilters({ filters, onChange }: BookingFiltersProps) {
  const { data: user } = useCurrentUser()
  const isAdmin = user?.role === 'admin'

  const properties = useQuery(propertyOptionsQueryOptions())
  const agents = useQuery({ ...agentOptionsQueryOptions(), enabled: isAdmin })

  function commit(patch: Partial<BookingListFilters>) {
    onChange({ ...filters, ...patch })
  }

  return (
    <form
      aria-label="Booking filters"
      onSubmit={(event) => {
        event.preventDefault()
      }}
      className="space-y-4 rounded-lg border border-slate-200 bg-white p-4"
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SelectFilter
          id="filter-property"
          label="Property"
          anyLabel="Any property"
          value={filters.property_id}
          options={(properties.data?.results ?? []).map((property) => ({
            value: property.id,
            label: property.title,
          }))}
          isPending={properties.isPending}
          isError={properties.isError}
          errorNote="Property list unavailable — the other filters still work."
          onChange={(property_id) => {
            commit({ property_id })
          }}
        />

        {/* Admin-only, and not because an `agent` is forbidden the control: their list is
            already scoped to their own bookings server-side, so the filter could only
            ever be a no-op or empty everything. `GET /users` is admin-only too, so the
            options do not exist for them either. */}
        {isAdmin ? (
          <SelectFilter
            id="filter-agent"
            label="Agent"
            anyLabel="Any agent"
            value={filters.agent_id}
            options={(agents.data?.results ?? []).map((agent) => ({
              value: agent.id,
              label: agent.name,
            }))}
            isPending={agents.isPending}
            isError={agents.isError}
            errorNote="Agent list unavailable — the other filters still work."
            onChange={(agent_id) => {
              commit({ agent_id })
            }}
          />
        ) : null}

        <div className="space-y-1">
          <label htmlFor="filter-date-from" className="block text-sm font-medium text-slate-700">
            Viewings from
          </label>
          <input
            id="filter-date-from"
            type="date"
            value={filters.date_from}
            onChange={(event) => {
              commit({ date_from: event.target.value })
            }}
            className={CONTROL_CLASS}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-date-to" className="block text-sm font-medium text-slate-700">
            Viewings to
          </label>
          <input
            id="filter-date-to"
            type="date"
            value={filters.date_to}
            onChange={(event) => {
              commit({ date_to: event.target.value })
            }}
            className={CONTROL_CLASS}
          />
        </div>
      </div>

      <fieldset>
        <legend className="text-sm font-medium text-slate-700">Status</legend>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
          {BOOKING_STATUSES.map((status) => (
            <label key={status} className="flex items-center gap-1.5 text-sm text-slate-700">
              <input
                type="checkbox"
                name="status"
                value={status}
                checked={filters.status.includes(status)}
                onChange={(event) => {
                  commit({ status: toggle(filters.status, status, event.target.checked) })
                }}
                className="size-4 rounded border-slate-300 text-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              />
              {BOOKING_STATUS_LABELS[status]}
            </label>
          ))}
        </div>
      </fieldset>

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

interface SelectFilterProps {
  id: string
  label: string
  anyLabel: string
  value: string
  options: { value: string; label: string }[]
  isPending: boolean
  isError: boolean
  errorNote: string
  onChange: (value: string) => void
}

/**
 * A failed option list degrades to a disabled control plus a note rather than to an
 * error state for the whole screen: the bookings themselves loaded, and the filters that
 * do not depend on a second request are still usable.
 *
 * The current value is kept as an option even when it is missing from the list, so a
 * shared URL naming a property beyond the 100-row cap still shows what is filtering the
 * table instead of silently reading as "Any property".
 */
function SelectFilter({
  id,
  label,
  anyLabel,
  value,
  options,
  isPending,
  isError,
  errorNote,
  onChange,
}: SelectFilterProps) {
  const known = options.some((option) => option.value === value)

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      <select
        id={id}
        value={value}
        disabled={isPending || isError}
        onChange={(event) => {
          onChange(event.target.value)
        }}
        className={CONTROL_CLASS}
      >
        <option value="">{isPending ? 'Loading…' : anyLabel}</option>
        {value && !known ? <option value={value}>{value}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {isError ? <p className="text-xs text-amber-800">{errorNote}</p> : null}
    </div>
  )
}

function toggle(selected: BookingStatus[], status: BookingStatus, checked: boolean): BookingStatus[] {
  return checked ? [...selected, status] : selected.filter((value) => value !== status)
}
