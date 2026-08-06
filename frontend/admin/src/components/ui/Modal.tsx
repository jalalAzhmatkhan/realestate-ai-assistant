import { useEffect, useId, useRef, type ReactNode } from 'react'

export interface ModalProps {
  open: boolean
  /** Escape, the backdrop, and the modal's own dismiss action all route through this. */
  onClose: () => void
  title: string
  children: ReactNode
  /** Action buttons, rendered right-aligned under the body. */
  footer: ReactNode
}

/**
 * UI/UX §4's `<Modal>`, for destructive confirmations only.
 *
 * F4 deferred this and shipped an inline two-step confirm instead, on the grounds that
 * a modal worth having needs a focus trap, focus restore, Escape handling and inert
 * background — too much to build for one reversible action. Two things changed here:
 * booking cancellation is *not* reversible (the slot is released; there is no un-cancel,
 * only a fresh booking that may no longer fit), and it has two call sites — a list row
 * and the detail screen.
 *
 * Built on the native `<dialog>` element, which is what makes the cost F4 weighed up
 * mostly disappear: `showModal()` supplies the focus trap, the focus restore on close,
 * the top layer, the backdrop and Escape natively, so this file only has to keep the
 * element in step with React state.
 */
export default function Modal({ open, onClose, title, children, footer }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return

    // Guarded both ways: `showModal()` on an already-open dialog throws
    // `InvalidStateError`, and StrictMode runs this effect twice in development.
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      // Escape's own default would close the element behind React's back, leaving
      // `open` true and the dialog unopenable until it was toggled twice.
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      // The dialog box itself is the padded child below, so a click landing on the
      // `<dialog>` element is a click on the backdrop around it.
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-slate-200 p-0 shadow-lg backdrop:bg-slate-900/40"
    >
      {/* Body only while open. A closed `<dialog>` is hidden, but its contents are still
          in the document — and a table renders one of these per row, so leaving them
          mounted would put twenty copies of a booking's details in the accessibility
          tree for every page of results. */}
      {open ? (
        <div className="space-y-4 p-5">
          <h2 id={titleId} className="text-base font-semibold text-slate-900">
            {title}
          </h2>
          <div className="text-sm text-slate-600">{children}</div>
          <div className="flex flex-wrap justify-end gap-2">{footer}</div>
        </div>
      ) : null}
    </dialog>
  )
}
