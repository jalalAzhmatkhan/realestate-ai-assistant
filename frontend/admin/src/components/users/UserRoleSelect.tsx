import { useState } from 'react'

import Button from '@/components/ui/Button'
import Modal from '@/components/ui/Modal'
import { isApiError } from '@/lib/api/errors'
import { useUpdateUser } from '@/lib/users/mutations'
import { USER_ROLE_LABELS, USER_ROLES, type User, type UserRole } from '@/lib/users/types'
import { SELF_ACCOUNT_NOTE_ID } from './SelfAccountNote'

export interface UserRoleSelectProps {
  user: User
  /** The caller's own row: every role change from `admin` is a self-demotion. */
  isSelf: boolean
}

const SELECT_CLASS =
  'w-full min-w-32 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 ' +
  'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-blue-600 ' +
  'disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500'

/**
 * UI/UX §5.6's inline `<select>` with an immediate confirm: picking a role stages it,
 * the modal names both ends of the change, and only "Yes, change it" sends the `PATCH`.
 * A role change moves what a person can see and do across the whole platform, which is
 * more than a misclick on a dense table row should be able to do silently.
 *
 * Dismissing the modal puts the control back to the role the backend still holds, so a
 * cancelled change never leaves the table claiming something that did not happen.
 */
export default function UserRoleSelect({ user, isSelf }: UserRoleSelectProps) {
  const [pending, setPending] = useState<UserRole | null>(null)
  const update = useUpdateUser(user.id)

  function dismiss() {
    setPending(null)
    update.reset()
  }

  return (
    <>
      <label htmlFor={`role-${user.id}`} className="sr-only">
        Role for {user.name}
      </label>
      <select
        id={`role-${user.id}`}
        // The staged value, so the control shows what is being confirmed rather than
        // snapping back for the lifetime of the modal.
        value={pending ?? user.role}
        // Self-demotion is the only change this row could express, and the backend
        // rejects it with `409 self_lockout_forbidden` — see SelfAccountNote.
        disabled={isSelf || update.isPending}
        aria-describedby={isSelf ? SELF_ACCOUNT_NOTE_ID : undefined}
        onChange={(event) => {
          setPending(event.target.value as UserRole)
        }}
        className={SELECT_CLASS}
      >
        {USER_ROLES.map((role) => (
          <option key={role} value={role}>
            {USER_ROLE_LABELS[role]}
          </option>
        ))}
      </select>

      <Modal
        open={pending !== null}
        onClose={dismiss}
        title="Change this user's role?"
        footer={
          <>
            <Button variant="ghost" disabled={update.isPending} onClick={dismiss}>
              Keep {USER_ROLE_LABELS[user.role].toLowerCase()}
            </Button>
            <Button
              autoFocus
              disabled={update.isPending}
              onClick={() => {
                if (!pending) return
                update.mutate(
                  { role: pending },
                  {
                    // Safe to drop the staged value here: the mutation writes the
                    // updated row into the cached list first, so `user.role` the select
                    // falls back to is already the role that was just confirmed.
                    onSuccess: () => {
                      setPending(null)
                    },
                  },
                )
              }}
            >
              {update.isPending ? 'Changing…' : 'Yes, change it'}
            </Button>
          </>
        }
      >
        {pending ? (
          <p>
            Change <span className="font-medium text-slate-900">{user.name}</span>&rsquo;s role from{' '}
            <span className="font-medium text-slate-900">{USER_ROLE_LABELS[user.role]}</span> to{' '}
            <span className="font-medium text-slate-900">{USER_ROLE_LABELS[pending]}</span>?
          </p>
        ) : null}
        <p className="mt-2">
          This changes what they can see and do across the platform, and takes effect on their next
          request.
        </p>

        {update.isError ? (
          <p
            role="alert"
            className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800"
          >
            {roleChangeErrorMessage(update.error)}
          </p>
        ) : null}
      </Modal>
    </>
  )
}

/**
 * `self_lockout_forbidden` and `user_not_found` are both already worded for a person by
 * the backend, so they are rendered as sent rather than reworded. The control is
 * disabled on the caller's own row, so the former should be unreachable from here — it
 * is still surfaced, because the client-side guard is a UX nicety and the backend is
 * what actually decides.
 */
function roleChangeErrorMessage(error: unknown): string {
  return isApiError(error) ? error.message : 'Could not change this role. Please try again.'
}
