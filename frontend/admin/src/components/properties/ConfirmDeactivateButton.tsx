import { useState } from 'react'

import Button from '@/components/ui/Button'

export interface ConfirmDeactivateButtonProps {
  onConfirm: () => void
  isPending: boolean
}

/**
 * An inline two-step confirm rather than UI/UX §4's `<Modal>`, which does not exist yet.
 *
 * A modal primitive worth shipping needs a focus trap, restore-on-close, Escape handling
 * and scroll locking — a real amount of code and test surface to build for a *single*
 * call site whose action is reversible (deactivating sets the listing back to `draft`;
 * it deletes nothing). F5's booking cancellation is the genuinely destructive
 * confirmation, and having two call sites then is what should shape that primitive.
 */
export default function ConfirmDeactivateButton({
  onConfirm,
  isPending,
}: ConfirmDeactivateButtonProps) {
  const [armed, setArmed] = useState(false)

  if (!armed) {
    return (
      <Button
        variant="secondary"
        onClick={() => {
          setArmed(true)
        }}
      >
        Deactivate
      </Button>
    )
  }

  return (
    <div
      role="group"
      aria-label="Confirm deactivation"
      className="flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2"
    >
      <p className="text-sm text-amber-900">Return this listing to draft?</p>
      {/* Not "Cancel": the form's own Cancel button is on screen at the same time, and
          two identically-labelled buttons doing different things is a misclick waiting
          to happen. */}
      <Button
        variant="ghost"
        disabled={isPending}
        onClick={() => {
          setArmed(false)
        }}
      >
        No, keep it
      </Button>
      {/* Focus follows the action: the button that was clicked has just unmounted, so
          without this a keyboard user is stranded at the top of the document. */}
      <Button autoFocus variant="secondary" disabled={isPending} onClick={onConfirm}>
        {isPending ? 'Deactivating…' : 'Yes, deactivate'}
      </Button>
    </div>
  )
}
