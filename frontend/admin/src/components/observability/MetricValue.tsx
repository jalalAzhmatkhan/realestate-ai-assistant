/**
 * Renders a nullable metric (`recall_at_k`'s entries, `mrr`, `abstention_rate`,
 * `identity_recall_at_1`) as its own component precisely so the null-vs-zero distinction
 * cannot be reintroduced by accident at a second call site: `null` means "not computed"
 * (a `failed` run never ran the metric), `0` means the metric ran and scored zero — the
 * task spec is explicit these must never collapse into the same "0"/"0.00" text.
 */
export interface MetricValueProps {
  value: number | null
  /** Formats a non-null value, e.g. `(v) => \`${(v * 100).toFixed(0)}%\`` for a ratio. */
  format?: (value: number) => string
}

const DEFAULT_FORMAT = (value: number): string => value.toFixed(2)

export default function MetricValue({ value, format = DEFAULT_FORMAT }: MetricValueProps) {
  return <>{value === null ? '—' : format(value)}</>
}
