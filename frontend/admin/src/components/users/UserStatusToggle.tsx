import { useState } from 'react'

import Button from '@/components/ui/Button'
import Modal from '@/components/ui/Modal'
import { isApiError } from '@/lib/api/errors'
import { useUpdateUser } from '@/lib/users/mutations'
import type { User } from '@/lib/users/types'
import { SELF_ACCOUNT_NOTE_ID } from './SelfAccountNote'

export interface UserStatusToggleProps {
  user: User
  /** The caller's own row: disabling it would lock them out with no recovery path. */
  isSelf: boolean
}

/**
 * Enable/disable, confirmed in both directions.
 *
 * Disabling is not a soft, reversible edit in practice: `get_current_user` re-reads
 * `status` on every request (`app/api/deps.py`), so it ends the person's live session
 * immediately, not at their next login. Enabling is confirmed for the mirror-image
 * reason — a misclick there silently restores access to an account somebody deliberately
 * shut off. Both sit in a dense table row next to a role `<select>` that also confirms;
 * making one of the two one-click would be the arbitrary choice, not this.
 *
 * There is no undo affordance to lean on instead: the app has no toast primitive, and a
 * row-level undo would still be a second `PATCH` the admin has to notice in time.
 */
export default function UserStatusToggle({ user, isSelf }: UserStatusToggleProps) {
  const [open, setOpen] = useState(false)
  const update = useUpdateUser(user.id)

  const disabling = user.status === 'active'
  const nextStatus = disabling ? 'disabled' : 'active'

  function dismiss() {
    setOpen(false)
    update.reset()
  }

  return (
    <>
      <Button
        variant="secondary"
        disabled={isSelf || update.isPending}
        aria-describedby={isSelf ? SELF_ACCOUNT_NOTE_ID : undefined}
        onClick={() => {
          setOpen(true)
        }}
      >
        {disabling ? 'Disable' : 'Enable'}
      </Button>

      <Modal
        open={open}
        onClose={dismiss}
        title={disabling ? 'Disable this account?' : 'Enable this account?'}
        footer={
          <>
            <Button variant="ghost" disabled={update.isPending} onClick={dismiss}>
              Keep as is
            </Button>
            <Button
              autoFocus
              variant={disabling ? 'secondary' : 'primary'}
              disabled={update.isPending}
              onClick={() => {
                update.mutate(
                  { status: nextStatus },
                  {
                    onSuccess: () => {
                      setOpen(false)
                    },
                  },
                )
              }}
            >
              {update.isPending
                ? disabling
                  ? 'Disabling…'
                  : 'Enabling…'
                : disabling
                  ? 'Yes, disable'
                  : 'Yes, enable'}
            </Button>
          </>
        }
      >
        <p>
          <span className="font-medium text-slate-900">{user.name}</span> ({user.email})
        </p>
        {disabling ? (
          <p className="mt-2">
            They are signed out on their next request and cannot log in again until someone
            re-enables them. Nothing is deleted — their bookings and listings stay exactly as they
            are.
          </p>
        ) : (
          <p className="mt-2">They can log in again immediately, with the role shown on this row.</p>
        )}

        {update.isError ? (
          <p
            role="alert"
            className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800"
          >
            {statusChangeErrorMessage(update.error)}
          </p>
        ) : null}
      </Modal>
    </>
  )
}

/** The backend words `self_lockout_forbidden` and `user_not_found` for a person already. */
function statusChangeErrorMessage(error: unknown): string {
  return isApiError(error) ? error.message : 'Could not update this account. Please try again.'
}
