import type { ReactNode } from 'react'

export interface EmptyStateProps {
  title: string
  description?: string
  /** Optional primary action, e.g. "Clear filters" or "Add a property" (UI/UX §5.2). */
  action?: ReactNode
}

export default function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-slate-300 px-6 py-12 text-center"
      data-testid="empty-state"
    >
      <p className="text-sm font-medium text-slate-900">{title}</p>
      {description ? <p className="max-w-md text-sm text-slate-500">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}
