import { useState, type FormEvent, type ReactNode } from 'react'

import Button from '@/components/ui/Button'
import { isApiError } from '@/lib/api/errors'
import {
  LISTING_TYPE_LABELS,
  LISTING_TYPES,
  PRICE_UNIT_LABELS,
  PRICE_UNITS,
  PROPERTY_STATUS_LABELS,
  PROPERTY_STATUSES,
  PROPERTY_TYPE_LABELS,
  PROPERTY_TYPES,
  type ListingType,
  type PriceUnit,
  type PropertyDetail,
  type PropertyStatus,
  type PropertyType,
  type PropertyWriteRequest,
} from '@/lib/properties/types'

export interface PropertyFormProps {
  /** Absent for create. Must be the full `GET /properties/{id}` record, never a list row. */
  initial?: PropertyDetail
  onSubmit: (payload: PropertyWriteRequest) => void
  onCancel: () => void
  isSubmitting: boolean
  /** The failed write, unnarrowed — 422 field errors are routed to their inputs. */
  error?: unknown
  submitLabel: string
  /** Deactivate, rendered by the detail screen only when the caller may edit. */
  secondaryActions?: ReactNode
}

/**
 * Every field the write contract accepts, in one form used by both create and edit,
 * because `PUT` is a full replacement rather than a patch — a form that rendered a
 * subset would blank whatever it omitted on the next save.
 */
interface Draft {
  title: string
  property_type: PropertyType
  listing_type: ListingType
  price: string
  currency: string
  price_unit: PriceUnit
  bedrooms: string
  bathrooms: string
  area_sqm: string
  address: string
  city: string
  latitude: string
  longitude: string
  amenities: string
  description: string
  status: PropertyStatus
  listed_date: string
}

export default function PropertyForm({
  initial,
  onSubmit,
  onCancel,
  isSubmitting,
  error,
  submitLabel,
  secondaryActions,
}: PropertyFormProps) {
  const [draft, setDraft] = useState<Draft>(() => (initial ? toDraft(initial) : blankDraft()))

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (isSubmitting) return
    onSubmit(toPayload(draft))
  }

  const strays = strayErrors(error)

  return (
    // Deliberately not `noValidate` (unlike LoginPage.tsx): these are number inputs, and
    // an empty one coerces to `Number('') === 0` in `toPayload` below rather than to an
    // empty string the backend would reject. `required` and the numeric `min`/`max`s are
    // what stop a blank field from silently becoming a legitimate-looking zero — e.g.
    // (0, 0) coordinates or a free listing — with a 201, not a validation error either
    // side would ever surface. LoginPage's fields are text, where a blank submits as `''`
    // and the backend's own 401/422 already renders next to the field; that failure mode
    // does not exist here, so its reasoning for skipping native validation does not
    // transfer. Every constraint below matches PropertyWriteRequest (backend/app/schemas/
    // property.py) except `area_sqm`'s `min="0"` vs the backend's `gt=0` — HTML has no
    // exclusive-minimum attribute, so `0` is deliberately let through client-side and
    // still correctly rejected by the backend's real 422.
    <form onSubmit={handleSubmit} className="space-y-6">
      {error ? (
        <div
          role="alert"
          className="space-y-1 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          <p>{isApiError(error) ? error.message : 'Could not save. Please try again.'}</p>
          {/* Anything the backend rejected that has no input on this form — an
              `agent_id` or a whole-body rule — would otherwise vanish silently. */}
          {strays.length > 0 ? (
            <ul className="list-disc pl-5">
              {strays.map((stray) => (
                <li key={`${stray.field}:${stray.message}`}>
                  {stray.field ? `${stray.field}: ${stray.message}` : stray.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <section className="grid gap-4 sm:grid-cols-2">
        <TextField
          id="title"
          label="Title"
          value={draft.title}
          onChange={(value) => {
            set('title', value)
          }}
          error={fieldErrorFor(error, 'title')}
          required
          className="sm:col-span-2"
        />

        <TextField
          id="address"
          label="Address"
          value={draft.address}
          onChange={(value) => {
            set('address', value)
          }}
          error={fieldErrorFor(error, 'address')}
          required
          className="sm:col-span-2"
        />

        <TextField
          id="city"
          label="City"
          value={draft.city}
          onChange={(value) => {
            set('city', value)
          }}
          error={fieldErrorFor(error, 'city')}
          required
        />

        <SelectField
          id="property_type"
          label="Property type"
          value={draft.property_type}
          options={PROPERTY_TYPES}
          labels={PROPERTY_TYPE_LABELS}
          onChange={(value) => {
            set('property_type', value)
          }}
          error={fieldErrorFor(error, 'property_type')}
        />

        <SelectField
          id="listing_type"
          label="Listing type"
          value={draft.listing_type}
          options={LISTING_TYPES}
          labels={LISTING_TYPE_LABELS}
          onChange={(value) => {
            set('listing_type', value)
          }}
          error={fieldErrorFor(error, 'listing_type')}
        />

        <SelectField
          id="status"
          label="Status"
          value={draft.status}
          options={PROPERTY_STATUSES}
          labels={PROPERTY_STATUS_LABELS}
          onChange={(value) => {
            set('status', value)
          }}
          error={fieldErrorFor(error, 'status')}
        />
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        <TextField
          id="price"
          label="Price"
          type="number"
          min="0"
          step="any"
          value={draft.price}
          onChange={(value) => {
            set('price', value)
          }}
          error={fieldErrorFor(error, 'price')}
          required
        />

        <TextField
          id="currency"
          label="Currency"
          value={draft.currency}
          onChange={(value) => {
            set('currency', value.toUpperCase())
          }}
          error={fieldErrorFor(error, 'currency')}
          hint="Three-letter code"
          required
        />

        <SelectField
          id="price_unit"
          label="Price unit"
          value={draft.price_unit}
          options={PRICE_UNITS}
          labels={PRICE_UNIT_LABELS}
          onChange={(value) => {
            set('price_unit', value)
          }}
          error={fieldErrorFor(error, 'price_unit')}
        />

        <TextField
          id="bedrooms"
          label="Bedrooms"
          type="number"
          min="0"
          step="1"
          value={draft.bedrooms}
          onChange={(value) => {
            set('bedrooms', value)
          }}
          error={fieldErrorFor(error, 'bedrooms')}
          required
        />

        <TextField
          id="bathrooms"
          label="Bathrooms"
          type="number"
          min="0"
          step="1"
          value={draft.bathrooms}
          onChange={(value) => {
            set('bathrooms', value)
          }}
          error={fieldErrorFor(error, 'bathrooms')}
          required
        />

        <TextField
          id="area_sqm"
          label="Area (m²)"
          type="number"
          min="0"
          step="any"
          value={draft.area_sqm}
          onChange={(value) => {
            set('area_sqm', value)
          }}
          error={fieldErrorFor(error, 'area_sqm')}
          required
        />

        <TextField
          id="latitude"
          label="Latitude"
          type="number"
          min="-90"
          max="90"
          step="any"
          value={draft.latitude}
          onChange={(value) => {
            set('latitude', value)
          }}
          error={fieldErrorFor(error, 'latitude')}
          required
        />

        <TextField
          id="longitude"
          label="Longitude"
          type="number"
          min="-180"
          max="180"
          step="any"
          value={draft.longitude}
          onChange={(value) => {
            set('longitude', value)
          }}
          error={fieldErrorFor(error, 'longitude')}
          required
        />

        {/* Resent on every edit, never left to the server default. `listed_date` is
            optional in the write contract and defaults to *today* when omitted — on a
            full-replacement PUT that would silently re-date an existing listing. */}
        <TextField
          id="listed_date"
          label="Listed date"
          type="date"
          value={draft.listed_date}
          onChange={(value) => {
            set('listed_date', value)
          }}
          error={fieldErrorFor(error, 'listed_date')}
          hint={initial ? undefined : 'Defaults to today'}
        />
      </section>

      <section className="space-y-4">
        <TextAreaField
          id="amenities"
          label="Amenities"
          rows={4}
          value={draft.amenities}
          onChange={(value) => {
            set('amenities', value)
          }}
          error={fieldErrorFor(error, 'amenities')}
          hint="One per line"
        />

        <TextAreaField
          id="description"
          label="Description"
          rows={6}
          value={draft.description}
          onChange={(value) => {
            set('description', value)
          }}
          error={fieldErrorFor(error, 'description')}
        />
      </section>

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
        {secondaryActions}
        <div className="ml-auto flex gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? 'Saving…' : submitLabel}
          </Button>
        </div>
      </div>
    </form>
  )
}

const CONTROL_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-blue-600 ' +
  'disabled:bg-slate-50 aria-[invalid=true]:border-red-400'

interface FieldShellProps {
  id: string
  label: string
  error?: string | undefined
  hint?: string | undefined
  className?: string | undefined
  children: ReactNode
}

function FieldShell({ id, label, error, hint, className, children }: FieldShellProps) {
  return (
    <div className={`space-y-1 ${className ?? ''}`}>
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      {children}
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

function describedBy(id: string, error?: string, hint?: string): string | undefined {
  if (error) return `${id}-error`
  return hint ? `${id}-hint` : undefined
}

interface TextFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  type?: 'text' | 'number' | 'date'
  error?: string | undefined
  hint?: string | undefined
  className?: string | undefined
  required?: boolean
  min?: string
  max?: string
  step?: string
}

function TextField({
  id,
  label,
  value,
  onChange,
  type = 'text',
  error,
  hint,
  className,
  required,
  min,
  max,
  step,
}: TextFieldProps) {
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} className={className}>
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        required={required}
        min={min}
        max={max}
        step={step}
        onChange={(event) => {
          onChange(event.target.value)
        }}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        className={CONTROL_CLASS}
      />
    </FieldShell>
  )
}

interface SelectFieldProps<T extends string> {
  id: string
  label: string
  value: T
  options: readonly T[]
  labels: Record<T, string>
  onChange: (value: T) => void
  error?: string | undefined
}

function SelectField<T extends string>({
  id,
  label,
  value,
  options,
  labels,
  onChange,
  error,
}: SelectFieldProps<T>) {
  return (
    <FieldShell id={id} label={label} error={error}>
      <select
        id={id}
        name={id}
        value={value}
        onChange={(event) => {
          // Every option is rendered from `options`, so the value is one of them.
          onChange(event.target.value as T)
        }}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error)}
        className={CONTROL_CLASS}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {labels[option]}
          </option>
        ))}
      </select>
    </FieldShell>
  )
}

interface TextAreaFieldProps {
  id: string
  label: string
  value: string
  rows: number
  onChange: (value: string) => void
  error?: string | undefined
  hint?: string | undefined
}

function TextAreaField({ id, label, value, rows, onChange, error, hint }: TextAreaFieldProps) {
  return (
    <FieldShell id={id} label={label} error={error} hint={hint}>
      <textarea
        id={id}
        name={id}
        rows={rows}
        value={value}
        onChange={(event) => {
          onChange(event.target.value)
        }}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        className={CONTROL_CLASS}
      />
    </FieldShell>
  )
}

function blankDraft(): Draft {
  return {
    title: '',
    property_type: 'apartment',
    listing_type: 'rent',
    price: '',
    currency: 'IDR',
    price_unit: 'per_month',
    bedrooms: '',
    bathrooms: '',
    area_sqm: '',
    address: '',
    city: '',
    latitude: '',
    longitude: '',
    amenities: '',
    description: '',
    // Matches the backend default: a create that publishes to every client by omission
    // is the wrong direction to fail in.
    status: 'draft',
    listed_date: '',
  }
}

function toDraft(property: PropertyDetail): Draft {
  return {
    title: property.title,
    property_type: property.property_type,
    listing_type: property.listing_type,
    price: String(property.price),
    currency: property.currency,
    price_unit: property.price_unit,
    bedrooms: String(property.bedrooms),
    bathrooms: String(property.bathrooms),
    area_sqm: String(property.area_sqm),
    address: property.address,
    city: property.city,
    latitude: String(property.latitude),
    longitude: String(property.longitude),
    amenities: property.amenities.join('\n'),
    description: property.description,
    status: property.status,
    listed_date: property.listed_date,
  }
}

function toPayload(draft: Draft): PropertyWriteRequest {
  return {
    title: draft.title.trim(),
    property_type: draft.property_type,
    listing_type: draft.listing_type,
    price: Number(draft.price),
    currency: draft.currency.trim(),
    price_unit: draft.price_unit,
    bedrooms: Number(draft.bedrooms),
    bathrooms: Number(draft.bathrooms),
    area_sqm: Number(draft.area_sqm),
    address: draft.address.trim(),
    city: draft.city.trim(),
    latitude: Number(draft.latitude),
    longitude: Number(draft.longitude),
    amenities: draft.amenities
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean),
    description: draft.description,
    status: draft.status,
    // Omitted rather than sent empty on create, so the backend stamps today.
    ...(draft.listed_date ? { listed_date: draft.listed_date } : {}),
  }
}

/**
 * Matches `amenities.0` onto the `amenities` input as well as an exact hit, so a
 * rejected list entry lands on the control that produced it instead of the stray list.
 */
function fieldErrorFor(error: unknown, name: string): string | undefined {
  if (!isApiError(error)) return undefined
  return error.fieldErrors.find(
    (candidate) => candidate.field === name || candidate.field.startsWith(`${name}.`),
  )?.message
}

const FORM_FIELDS = Object.keys(blankDraft())

function strayErrors(error: unknown) {
  if (!isApiError(error)) return []
  return error.fieldErrors.filter((candidate) => {
    const [root] = candidate.field.split('.')
    return !root || !FORM_FIELDS.includes(root)
  })
}
