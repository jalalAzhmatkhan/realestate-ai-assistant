import type { Page } from '@/lib/api/pagination'
import type { PropertyDetail, PropertySummary } from '@/lib/properties/types'

export function buildSummary(overrides: Partial<PropertySummary> = {}): PropertySummary {
  return {
    id: 'prop-1',
    title: '2BR Apartment, Kemang',
    property_type: 'apartment',
    listing_type: 'rent',
    price: 3_200_000,
    currency: 'IDR',
    price_unit: 'per_month',
    bedrooms: 2,
    bathrooms: 1,
    area_sqm: 68,
    address: 'Jl. Kemang Raya 12',
    city: 'Jakarta',
    status: 'active',
    agent_id: 'u-agent',
    distance_km: null,
    ...overrides,
  }
}

/**
 * Carries the five fields `PropertySummary` does not, which is the whole reason the
 * detail screen refetches rather than reading a list row.
 */
export function buildDetail(overrides: Partial<PropertyDetail> = {}): PropertyDetail {
  return {
    id: 'prop-1',
    title: '2BR Apartment, Kemang',
    property_type: 'apartment',
    listing_type: 'rent',
    price: 3_200_000,
    currency: 'IDR',
    price_unit: 'per_month',
    bedrooms: 2,
    bathrooms: 1,
    area_sqm: 68,
    address: 'Jl. Kemang Raya 12',
    city: 'Jakarta',
    latitude: -6.2607,
    longitude: 106.8135,
    amenities: ['Pool', 'Gym'],
    agent_id: 'u-agent',
    status: 'active',
    description: 'Quiet corner unit with a garden view.',
    listed_date: '2026-01-15',
    ...overrides,
  }
}

export function buildPage(results: PropertySummary[], overrides: Partial<Page<PropertySummary>> = {}) {
  return {
    results,
    page: 1,
    page_size: 20,
    total: results.length,
    total_pages: 1,
    ...overrides,
  }
}
