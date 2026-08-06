const UNIT_SUFFIX: Record<string, string> = {
  per_month: '/mo',
  per_year: '/yr',
  total: '',
}

/**
 * Exact amounts rather than the wireframe's compact "Rp 3.2M" (UI/UX §5.2): staff
 * compare and verify prices in this table daily, and rounding 3,249,000 to "3.2M" hides
 * the digits they are checking.
 */
export function formatPrice(price: number, currency: string, priceUnit: string): string {
  return `${formatCurrency(price, currency)}${UNIT_SUFFIX[priceUnit] ?? ''}`
}

function formatCurrency(price: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(price)
  } catch {
    // `currency` is any 3-character string as far as the contract is concerned, and
    // Intl throws on a code it does not know — a bad seed row must not blank the table.
    return `${currency} ${new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(price)}`
  }
}
