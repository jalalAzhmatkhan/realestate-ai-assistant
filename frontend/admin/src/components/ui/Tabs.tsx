export interface TabItem {
  id: string
  label: string
}

export interface TabsProps {
  items: readonly TabItem[]
  active: string
  onChange: (id: string) => void
  /** Accessible name for the tablist, e.g. "Observability sections". */
  label: string
}

const TAB_BASE =
  'inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium ' +
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600'

/**
 * A plain ARIA tablist, not a router-nested layout — the observability page is the only
 * screen with more than one logical section on it, so this stays a small shared
 * primitive rather than pulling in a routing-level tab abstraction for one caller.
 * Selection lives in the caller's URL state (`?tab=`), the same way every list filter
 * in this app does, so a tab is linkable and survives a refresh.
 */
export default function Tabs({ items, active, onChange, label }: TabsProps) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-slate-200">
      {items.map((item) => {
        const isActive = item.id === active
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => {
              onChange(item.id)
            }}
            className={`${TAB_BASE} ${
              isActive
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
