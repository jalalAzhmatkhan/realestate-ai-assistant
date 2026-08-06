/**
 * Every datetime on the wire is a **UTC instant**, never the listing's local offset.
 * `UtcDateTime` (backend `app/models/types.py`) normalizes to UTC on write and
 * re-attaches UTC on read, so a slot seeded as `13:00+07:00` is returned as
 * `2026-08-08T06:00:00Z`. Rendering the literal wall clock out of that string would
 * show 06:00 for a 13:00 viewing — a seven-hour booking error, not a formatting quirk.
 *
 * So every formatter here converts the instant into the *viewer's* timezone, which is
 * the only timezone the contract exposes, and the long form names it explicitly so a
 * reader in another zone can tell which clock they are looking at.
 */

const SHORT = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const LONG = new Intl.DateTimeFormat('en-GB', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZoneName: 'short',
})

/** Table cells and suggested-alternative buttons, e.g. `Sat 8 Aug, 13:00`. */
export function formatSlotTime(iso: string): string {
  return format(SHORT, iso)
}

/** Detail screens, e.g. `Saturday, 8 August 2026, 13:00 GMT+7`. */
export function formatSlotTimeLong(iso: string): string {
  return format(LONG, iso)
}

const DATE_ONLY = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

/** A timestamp where only the day matters, e.g. `15 Jan 2026`. */
export function formatDate(iso: string): string {
  return format(DATE_ONLY, iso)
}

/** Value for an `<input type="datetime-local">`, in the viewer's own timezone. */
export function toDateTimeLocalValue(iso: string): string {
  const instant = new Date(iso)
  if (Number.isNaN(instant.getTime())) return ''

  const offsetMinutes = instant.getTimezoneOffset()
  return new Date(instant.getTime() - offsetMinutes * 60_000).toISOString().slice(0, 16)
}

/**
 * `<input type="datetime-local">` -> the ISO instant the backend expects. The input's
 * value carries no offset and is read as local time, which is what makes it round-trip
 * with `toDateTimeLocalValue` above.
 */
export function fromDateTimeLocalValue(value: string): string | null {
  if (!value) return null
  const instant = new Date(value)
  return Number.isNaN(instant.getTime()) ? null : instant.toISOString()
}

/**
 * `YYYY-MM-DD` -> the instant at that local day's first or last millisecond.
 *
 * The bookings filter is an inclusive range over `slot_time` (backend
 * `app/booking/queries.py`), and `slot_time` is UTC — sending the bare date would
 * silently shift the window by the viewer's offset and drop the evening viewings on
 * the last day of the range.
 */
export function dayBoundaryToIso(date: string, edge: 'start' | 'end'): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null
  const instant = new Date(`${date}T${edge === 'start' ? '00:00:00.000' : '23:59:59.999'}`)
  return Number.isNaN(instant.getTime()) ? null : instant.toISOString()
}

function format(formatter: Intl.DateTimeFormat, iso: string): string {
  const instant = new Date(iso)
  // A malformed datetime is a backend contract break, not something the user can act
  // on — showing the raw string beats "Invalid Date" for whoever has to report it.
  return Number.isNaN(instant.getTime()) ? iso : formatter.format(instant)
}
