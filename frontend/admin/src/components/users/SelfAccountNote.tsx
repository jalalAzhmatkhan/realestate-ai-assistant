/**
 * The one visible explanation for why the caller's own row offers no role change and no
 * disable, referenced by `aria-describedby` from both of those controls.
 *
 * F4's precedent was "hide, don't disable, when the action does not apply" — a `draft`
 * listing has nothing to deactivate, so the button is simply absent. That reasoning does
 * not carry here: disabling and demoting *do* apply to every other row, so an admin
 * finding their own row alone missing two controls would read it as a rendering bug
 * rather than as a rule. This row is about the caller themselves, which is exactly the
 * case that benefits from being told why.
 */
export const SELF_ACCOUNT_NOTE_ID = 'self-account-note'

export default function SelfAccountNote() {
  return (
    <p id={SELF_ACCOUNT_NOTE_ID} className="text-xs text-slate-500">
      This is your account — you cannot change your own role or disable yourself.
    </p>
  )
}
