import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'

import Button from '@/components/ui/Button'
import { isApiError } from '@/lib/api/errors'
import { useLogin } from '@/lib/auth/useLogin'

/**
 * Fixed copy rather than reading `error.message`: the backend's default wording happens
 * to match today, but `invalid_credentials` answering an unknown email and a wrong
 * password identically is the actual guarantee — pinning the string here means this
 * screen can never leak which half was wrong even if the backend's wording changes
 * without this file being touched.
 */
const INVALID_CREDENTIALS_MESSAGE = 'Incorrect email or password.'

const FIELD_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-0 ' +
  'focus-visible:outline-blue-600 disabled:bg-slate-50'

/** No self-service signup and no password reset in MVP — accounts are provisioned. */
export default function LoginPage() {
  const navigate = useNavigate()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const error = login.error
  const emailError = isApiError(error) ? error.fieldError('email') : undefined
  const passwordError = isApiError(error) ? error.fieldError('password') : undefined

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (login.isPending) return

    login.mutate(
      { email, password },
      {
        // Not `/login`'s own concern who may be here: a `client` holds a valid session
        // from the chat surface, and AppLayout's staff gate is the one place that
        // decision is made. Rejecting a role here as well would be a second, drifting
        // copy of the same rule.
        onSuccess: () => void navigate('/', { replace: true }),
      },
    )
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-4 sm:p-6">
      <div className="w-full max-w-sm space-y-6 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-center text-xl font-semibold text-slate-900">Real Estate Admin</h1>

        {error ? (
          <p
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
          >
            {toLoginMessage(error)}
          </p>
        ) : null}

        {/* `noValidate`: the backend is the only validator here. It accepts an address
            with surrounding whitespace (it strips) and answers a malformed one with the
            same 401 as an unknown one — native constraint validation would reject both
            with an unstyleable bubble the backend would never have sent. An empty submit
            costs one round trip and comes back as a 422 rendered next to the field. */}
        <form className="space-y-4" onSubmit={handleSubmit} noValidate>
          <div className="space-y-1">
            <label htmlFor="email" className="block text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => {
                setEmail(event.target.value)
              }}
              disabled={login.isPending}
              aria-invalid={emailError ? true : undefined}
              aria-describedby={emailError ? 'email-error' : undefined}
              className={FIELD_CLASS}
            />
            {emailError ? (
              <p id="email-error" className="text-sm text-red-700">
                {emailError}
              </p>
            ) : null}
          </div>

          <div className="space-y-1">
            <label htmlFor="password" className="block text-sm font-medium text-slate-700">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => {
                setPassword(event.target.value)
              }}
              disabled={login.isPending}
              aria-invalid={passwordError ? true : undefined}
              aria-describedby={passwordError ? 'password-error' : undefined}
              className={FIELD_CLASS}
            />
            {passwordError ? (
              <p id="password-error" className="text-sm text-red-700">
                {passwordError}
              </p>
            ) : null}
          </div>

          <Button type="submit" className="w-full" disabled={login.isPending}>
            {login.isPending ? 'Signing in…' : 'Log in'}
          </Button>
        </form>
      </div>
    </main>
  )
}

/**
 * Everything except the 401 comes straight from `ApiError`, which already normalizes
 * the backend's three body shapes plus a dead network — so `account_disabled` shows the
 * backend's own wording, a 422 shows the field summary, and an unparseable 5xx shows
 * the generic sentence, without this screen re-parsing anything.
 */
function toLoginMessage(error: unknown): string {
  if (!isApiError(error)) return 'Could not sign you in. Please try again.'
  if (error.status === 401) return INVALID_CREDENTIALS_MESSAGE
  return error.message
}
