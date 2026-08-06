import type { ReactNode } from 'react'

import { formatPrice } from '@/lib/properties/format'
import {
  LISTING_TYPE_LABELS,
  PROPERTY_TYPE_LABELS,
  type PropertyDetail,
} from '@/lib/properties/types'

/**
 * What an agent sees on a colleague's listing, and what any non-owner sees. The backend
 * serves the same `PropertyDetail` either way — it scopes *visibility* wider than
 * *editability* on purpose — so the difference here is genuinely presentational.
 */
export default function PropertyReadOnlyView({ property }: { property: PropertyDetail }) {
  return (
    <div className="space-y-6 rounded-lg border border-slate-200 bg-white p-4">
      <dl className="grid gap-4 sm:grid-cols-2">
        <Row label="Address">{property.address}</Row>
        <Row label="City">{property.city}</Row>
        <Row label="Property type">{PROPERTY_TYPE_LABELS[property.property_type]}</Row>
        <Row label="Listing type">{LISTING_TYPE_LABELS[property.listing_type]}</Row>
        <Row label="Price">
          {formatPrice(property.price, property.currency, property.price_unit)}
        </Row>
        <Row label="Listed">{property.listed_date}</Row>
        <Row label="Bedrooms">{property.bedrooms}</Row>
        <Row label="Bathrooms">{property.bathrooms}</Row>
        <Row label="Area">{property.area_sqm} m²</Row>
        <Row label="Location">
          {property.latitude}, {property.longitude}
        </Row>
      </dl>

      <div className="space-y-1">
        <h2 className="text-sm font-medium text-slate-500">Amenities</h2>
        {property.amenities.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {property.amenities.map((amenity) => (
              <li
                key={amenity}
                className="rounded-full bg-slate-100 px-2 py-0.5 text-sm text-slate-700"
              >
                {amenity}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-400">None listed</p>
        )}
      </div>

      <div className="space-y-1">
        <h2 className="text-sm font-medium text-slate-500">Description</h2>
        <p className="text-sm whitespace-pre-wrap text-slate-700">
          {property.description || <span className="text-slate-400">No description</span>}
        </p>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm font-medium text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-900">{children}</dd>
    </div>
  )
}
