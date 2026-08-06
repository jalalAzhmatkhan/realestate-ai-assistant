import { useState, type FormEvent, type ReactNode } from 'react'

import Button from '@/components/ui/Button'
import Modal from '@/components/ui/Modal'
import { isApiError } from '@/lib/api/errors'
import { useCreateUser } from '@/lib/users/mutations'
import {
  EMAIL_ALREADY_EXISTS_CODE,
  MAX_EMAIL_LENGTH,
  MAX_NAME_LENGTH,
  MAX_PASSWORD_LENGTH,
  MIN_PASSWORD_LENGTH,
  USER_ROLE_LABELS,
  USER_ROLES,
  USER_STATUS_LABELS,
  USER_STATUSES,
  type User,
  type UserCreateRequest,
  type UserRole,
  type UserStatus,
} from '@/lib/users/types'

export interface CreateUserModalProps {
  open: boolean
  onClose: () => void
  onCreated: (user: User) => void
}

interface Draft {
  name: string
  email: string
  password: string
  role: UserRole
  status: UserStatus
}

const CONTROL_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-blue-600 ' +
  'disabled:bg-slate-50 aria-[invalid=true]:border-red-400'

/**
 * A modal rather than a `/users/new` route, unlike the property create screen.
 *
 * Three things separate the two. This form is five short fields against that form's
 * seventeen; it fetches nothing on mount, so there is no loading state a route would
 * organize; and the wireframe (UI/UX §5.6) is a single screen whose sitemap entry has no
 * `/users/new` sibling. Keeping the list behind the dialog also means the new row appears
 * where the admin was already looking, with their filters and page intact, instead of
 * after a round trip through a screen that discards them.
 *
 * §4 restricts `<Modal>` to "destructive/state-changing confirmations, not primary
 * navigation" — the constraint being that a modal must not be how you *reach* a screen.
 * A create form the list opens and closes in place is not navigation.
 */
export default function CreateUserModal({ open, onClose, onCreated }: CreateUserModalProps) {
  const [draft, setDraft] = useState<Draft>(blankDraft)
  const create = useCreateUser()

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  function dismiss() {
    // The password never outlives the dialog, and a reopened form starts clean rather
    // than holding a half-typed credential for the rest of the session.
    setDraft(blankDraft())
    create.reset()
    onClose()
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (create.isPending) return

    create.mutate(toPayload(draft), {
      onSuccess: (user) => {
        setDraft(blankDraft())
        create.reset()
        onCreated(user)
      },
    })
  }

  const emailError = emailErrorFor(create.error)
  const banner = bannerMessage(create.error)

  return (
    <Modal
      open={open}
      onClose={dismiss}
      title="Add user"
      footer={
        <>
          <Button variant="ghost" disabled={create.isPending} onClick={dismiss}>
            Cancel
          </Button>
          <Button type="submit" form={FORM_ID} disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create user'}
          </Button>
        </>
      }
    >
      {/* Native constraint validation is left on, unlike LoginPage. Every field here is
          genuinely required, and `type="email"` is marginally stricter than the backend
          (which takes any 3–320 character string, matching login's own leniency) — a
          deliberate client-side narrowing, since an address that is not an address can
          never be logged in with. The backend remains the validator that decides: its
          422s land on these same fields below. */}
      <form id={FORM_ID} onSubmit={handleSubmit} className="space-y-4">
        {banner ? (
          <p
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
          >
            {banner}
          </p>
        ) : null}

        <Field id="new-user-name" label="Name" error={fieldErrorFor(create.error, 'name')}>
          {(props) => (
            <input
              {...props}
              type="text"
              autoComplete="off"
              required
              maxLength={MAX_NAME_LENGTH}
              value={draft.name}
              disabled={create.isPending}
              onChange={(event) => {
                set('name', event.target.value)
              }}
            />
          )}
        </Field>

        <Field id="new-user-email" label="Email" error={emailError}>
          {(props) => (
            <input
              {...props}
              type="email"
              autoComplete="off"
              required
              maxLength={MAX_EMAIL_LENGTH}
              value={draft.email}
              disabled={create.isPending}
              onChange={(event) => {
                set('email', event.target.value)
              }}
            />
          )}
        </Field>

        <Field
          id="new-user-password"
          label="Temporary password"
          hint={`At least ${String(MIN_PASSWORD_LENGTH)} characters. Share it with them out of band.`}
          error={fieldErrorFor(create.error, 'password')}
        >
          {(props) => (
            <input
              {...props}
              type="password"
              // Not `current-password`: this form sets somebody else's credential, and
              // offering the admin's own saved password here would be the wrong fill.
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              maxLength={MAX_PASSWORD_LENGTH}
              value={draft.password}
              disabled={create.isPending}
              onChange={(event) => {
                set('password', event.target.value)
              }}
            />
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="new-user-role" label="Role" error={fieldErrorFor(create.error, 'role')}>
            {(props) => (
              <select
                {...props}
                value={draft.role}
                disabled={create.isPending}
                onChange={(event) => {
                  set('role', event.target.value as UserRole)
                }}
              >
                {USER_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {USER_ROLE_LABELS[role]}
                  </option>
                ))}
              </select>
            )}
          </Field>

          <Field id="new-user-status" label="Status" error={fieldErrorFor(create.error, 'status')}>
            {(props) => (
              <select
                {...props}
                value={draft.status}
                disabled={create.isPending}
                onChange={(event) => {
                  set('status', event.target.value as UserStatus)
                }}
              >
                {USER_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {USER_STATUS_LABELS[status]}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>
      </form>
    </Modal>
  )
}

/** The footer's submit button lives outside the `<form>`, so it submits by `form=`. */
const FORM_ID = 'create-user-form'

interface FieldProps {
  id: string
  label: string
  hint?: string
  error?: string | undefined
  children: (props: {
    id: string
    name: string
    className: string
    'aria-invalid': true | undefined
    'aria-describedby': string | undefined
  }) => ReactNode
}

function Field({ id, label, hint, error, children }: FieldProps) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      {children({
        id,
        name: id,
        className: CONTROL_CLASS,
        'aria-invalid': error ? true : undefined,
        'aria-describedby': error ? `${id}-error` : hint ? `${id}-hint` : undefined,
      })}
      {hint && !error ? (
        <p id={`${id}-hint`} className="text-xs text-slate-500">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="text-sm text-red-700">
          {error}
        </p>
      ) : null}
    </div>
  )
}

function blankDraft(): Draft {
  return {
    name: '',
    email: '',
    password: '',
    // Least privilege by default: a create that hands out `admin` by nobody touching the
    // control is the wrong direction to fail in.
    role: 'client',
    status: 'active',
  }
}

function toPayload(draft: Draft): UserCreateRequest {
  return {
    name: draft.name.trim(),
    // The backend lowercases and strips on its side; trimming here keeps what the admin
    // sees in the field consistent with the address the account ends up with.
    email: draft.email.trim(),
    // Never trimmed: leading or trailing whitespace is a legitimate part of a password.
    password: draft.password,
    role: draft.role,
    status: draft.status,
  }
}

function fieldErrorFor(error: unknown, name: string): string | undefined {
  return isApiError(error) ? error.fieldError(name) : undefined
}

/**
 * The one error that is field-level without being a 422. `email_already_exists` is a
 * `409` carrying `detail.code` rather than FastAPI's field list, so `fieldErrors` is
 * empty and the generic 422 routing above cannot see it — but it is unambiguously about
 * this one input, and leaving it in the banner would make the admin hunt for which field
 * to fix.
 */
function emailErrorFor(error: unknown): string | undefined {
  if (isApiError(error) && error.code === EMAIL_ALREADY_EXISTS_CODE) return error.message
  return fieldErrorFor(error, 'email')
}

/** Suppressed when the failure is already shown against a field, to avoid saying it twice. */
function bannerMessage(error: unknown): string | undefined {
  if (!error) return undefined
  if (!isApiError(error)) return 'Could not create this user. Please try again.'
  if (error.code === EMAIL_ALREADY_EXISTS_CODE) return undefined
  return error.message
}
