/**
 * Mirrors `app/schemas/property.py` and the `Literal` unions in
 * `app/models/property.py`. Wire names throughout — a camelCase rename layer would only
 * add a place for the two sides to drift.
 */

export const PROPERTY_TYPES = ['apartment', 'house', 'studio', 'townhouse', 'villa'] as const
export const LISTING_TYPES = ['sale', 'rent'] as const
export const PROPERTY_STATUSES = ['active', 'draft', 'under_offer', 'sold'] as const
export const PRICE_UNITS = ['per_month', 'per_year', 'total'] as const

export type PropertyType = (typeof PROPERTY_TYPES)[number]
export type ListingType = (typeof LISTING_TYPES)[number]
export type PropertyStatus = (typeof PROPERTY_STATUSES)[number]
export type PriceUnit = (typeof PRICE_UNITS)[number]

export const PROPERTY_TYPE_LABELS: Record<PropertyType, string> = {
  apartment: 'Apartment',
  house: 'House',
  studio: 'Studio',
  townhouse: 'Townhouse',
  villa: 'Villa',
}

export const LISTING_TYPE_LABELS: Record<ListingType, string> = {
  sale: 'For sale',
  rent: 'For rent',
}

export const PROPERTY_STATUS_LABELS: Record<PropertyStatus, string> = {
  active: 'Active',
  draft: 'Draft',
  under_offer: 'Under offer',
  sold: 'Sold',
}

export const PRICE_UNIT_LABELS: Record<PriceUnit, string> = {
  per_month: 'per month',
  per_year: 'per year',
  total: 'total',
}

/** Item type of `GET /properties` — deliberately lossy, see `PropertyDetail`. */
export interface PropertySummary {
  id: string
  title: string
  property_type: PropertyType
  listing_type: ListingType
  price: number
  currency: string
  price_unit: string
  bedrooms: number
  bathrooms: number
  area_sqm: number
  address: string
  city: string
  status: PropertyStatus
  agent_id: string | null
  /** Only set when the caller supplied a geo filter, which this dashboard does not. */
  distance_km: number | null
}

/**
 * `GET /properties/{id}`, and the response of every write.
 *
 * Five fields `PropertySummary` lacks — `latitude`, `longitude`, `amenities`,
 * `description`, `listed_date` — and every one of them is required by
 * `PropertyWriteRequest`. That is why the edit form is never hydrated from a list row.
 */
export interface PropertyDetail {
  id: string
  title: string
  property_type: PropertyType
  listing_type: ListingType
  price: number
  currency: string
  price_unit: PriceUnit
  bedrooms: number
  bathrooms: number
  area_sqm: number
  address: string
  city: string
  latitude: number
  longitude: number
  amenities: string[]
  agent_id: string | null
  status: PropertyStatus
  description: string
  /** ISO `YYYY-MM-DD`. */
  listed_date: string
}

/**
 * Body of both `POST /properties` and `PUT /properties/{id}`. The `PUT` is a genuine
 * full replacement, so every field is sent on every save, never a diff.
 *
 * `agent_id` is omitted on purpose: the backend forces it to the caller's own id for an
 * `agent`, and this task builds no admin agent-assignment control.
 */
export interface PropertyWriteRequest {
  title: string
  property_type: PropertyType
  listing_type: ListingType
  price: number
  currency: string
  price_unit: PriceUnit
  bedrooms: number
  bathrooms: number
  area_sqm: number
  address: string
  city: string
  latitude: number
  longitude: number
  amenities: string[]
  description: string
  status: PropertyStatus
  /** Omitted on create so the backend stamps today; always resent on edit (see PropertyForm). */
  listed_date?: string
}
