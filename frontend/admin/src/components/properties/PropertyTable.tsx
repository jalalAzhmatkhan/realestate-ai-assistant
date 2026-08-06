import { Link, useNavigate } from 'react-router'

import { readSort, toggleSort, type SortableField } from '@/lib/properties/filters'
import { formatPrice } from '@/lib/properties/format'
import { PROPERTY_TYPE_LABELS, type PropertySummary } from '@/lib/properties/types'
import StatusBadge from './StatusBadge'

export interface PropertyTableProps {
  properties: PropertySummary[]
  sort: string
  onSortChange: (sort: string) => void
}

const COLUMNS: { key: string; label: string; sortBy?: SortableField; numeric?: boolean }[] = [
  { key: 'title', label: 'Title', sortBy: 'title' },
  { key: 'city', label: 'City', sortBy: 'city' },
  { key: 'property_type', label: 'Type' },
  { key: 'price', label: 'Price', sortBy: 'price', numeric: true },
  { key: 'bedrooms', label: 'Bed', numeric: true },
  { key: 'status', label: 'Status' },
]

const CELL = 'px-3 py-2 text-sm text-slate-700'

/**
 * Deliberately not a generic `<DataTable>`. This is the first of the three list screens
 * (F4/F5/F6) and the only one that exists — the shared abstraction is worth extracting
 * once the second screen shows which parts are actually common, not guessed at from one.
 */
export default function PropertyTable({ properties, sort, onSortChange }: PropertyTableProps) {
  const navigate = useNavigate()
  const active = readSort(sort)

  return (
    // The design doc rules out a card layout below laptop width (§6), so a narrow screen
    // scrolls the table sideways rather than getting a different, untested rendering.
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[48rem] border-collapse">
        <thead className="border-b border-slate-200 bg-slate-50">
          <tr>
            {COLUMNS.map((column) => {
              const sortBy = column.sortBy
              const isActive = sortBy === active.field
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sortBy ? (isActive ? ariaSort(active.direction) : 'none') : undefined}
                  className={`px-3 py-2 text-xs font-semibold tracking-wide text-slate-600 uppercase ${
                    column.numeric ? 'text-right' : 'text-left'
                  }`}
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
          {properties.map((property) => (
            <tr
              key={property.id}
              // Convenience only. The title cell is a real link, so the row is reachable
              // by keyboard and announced as navigation without this handler.
              onClick={(event) => {
                if (event.target instanceof HTMLElement && event.target.closest('a')) return
                void navigate(`/properties/${property.id}`)
              }}
              className="cursor-pointer hover:bg-slate-50"
            >
              <td className={CELL}>
                <Link
                  to={`/properties/${property.id}`}
                  className="font-medium text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                >
                  {property.title}
                </Link>
                <span className="block text-xs text-slate-500">{property.address}</span>
              </td>
              <td className={CELL}>{property.city}</td>
              <td className={CELL}>{PROPERTY_TYPE_LABELS[property.property_type]}</td>
              <td className={`${CELL} text-right whitespace-nowrap`}>
                {formatPrice(property.price, property.currency, property.price_unit)}
              </td>
              <td className={`${CELL} text-right`}>{property.bedrooms}</td>
              <td className={CELL}>
                <StatusBadge status={property.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ariaSort(direction: 'asc' | 'desc'): 'ascending' | 'descending' {
  return direction === 'asc' ? 'ascending' : 'descending'
}
