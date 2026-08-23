/* Formatting helpers. Money is Indian-grouped (lakh/crore) because the data is INR. */

const inr = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function money(value: number): string {
  const sign = value < 0 ? '-' : ''
  return sign + inr.format(Math.abs(value))
}

export function compactMoney(value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)} Cr`
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)} L`
  return sign + inr.format(abs)
}

/** A grouped whole number - token counts, call counts. Indian grouping, to
 *  match the money formatter above rather than mixing conventions on one page. */
const grouped = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

export function count(value: number): string {
  return grouped.format(Math.round(value))
}

/** Hours to one decimal. 7.2 h reads as an estimate, which it is. */
export function hours(value: number): string {
  return `${value.toFixed(1)} h`
}

export function pct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`
}

export function shortDate(iso: string): string {
  const [y, m, d] = iso.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${d} ${months[Number(m) - 1] ?? m} ${y.slice(2)}`
}

/** Turns engine and LLM slugs into desk language. */
export function humanise(slug: string): string {
  const map: Record<string, string> = {
    // exception kinds
    duplicate: 'Duplicate',
    composite_candidate: 'Batched settlement',
    fuzzy_candidate: 'Reference mismatch',
    below_auto_threshold: 'Below your floor',
    unmatched_ledger: 'Not settled',
    unmatched_bank: 'Unexplained credit',
    // link methods
    exact_reference_amount_date: 'Exact',
    amount_rounding: 'Rounding',
    date_delay: 'Settlement delay',
    fee_adjusted: 'Gateway fee',
    late_settlement: 'Late settlement',
    refund_reversal: 'Reversal',
    composite_many_to_one: 'Batched',
    composite_one_to_many: 'Split payout',
    fuzzy_reference: 'Fuzzy reference',
    // llm categories
    fee_adjustment: 'Fee adjustment',
    split_payment: 'Split payment',
    reference_mismatch: 'Reference mismatch',
    refund: 'Refund',
    orphan_bank: 'Unexplained credit',
    orphan_ledger: 'Never settled',
    other: 'Other',
    // ground-truth categories
    clean_exact: 'Clean match',
    date_shift: 'Settlement delay',
    fee_deducted: 'Fee deducted',
    duplicate_ledger: 'Duplicate entry',
    split_batch: 'Batched / split',
    reference_typo: 'Reference typo',
    refund_reversal_gt: 'Refund pair',
    ambiguous_decoy: 'Ambiguous decoy',
    partial_settlement: 'Short settlement',
    narration_only_ref: 'Reference in narration',
    late_settlement_gt: 'Late settlement',
  }
  if (map[slug]) return map[slug]
  return slug.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

/**
 * Did a model actually answer for this exception?
 *
 * Deliberately accepts the rule-based stand-in as well as Groq. Checking only
 * for 'groq' is what made every mock verdict render as "model unavailable" and
 * fall back to the engine note - the answer was there, the UI just did not
 * recognise who wrote it.
 *
 * Whether it was the real model or the stand-in is a *labelling* question, and
 * every surface that shows a verdict answers it separately with a badge. It is
 * not a question about whether an answer exists.
 */
export function hasVerdict(source: string | undefined | null): boolean {
  return source === 'groq' || source === 'mock'
}

export type Tone = 'pine' | 'ochre' | 'oxblood' | 'slate'

export function confidenceTone(value: number): Tone {
  if (value >= 0.85) return 'pine'
  if (value >= 0.6) return 'ochre'
  return 'oxblood'
}

export function kindTone(kind: string): Tone {
  if (kind === 'duplicate' || kind === 'below_auto_threshold') return 'ochre'
  if (kind === 'composite_candidate' || kind === 'fuzzy_candidate') return 'ochre'
  if (kind === 'unmatched_ledger' || kind === 'unmatched_bank') return 'oxblood'
  return 'slate'
}

export const toneClasses: Record<Tone, { text: string; bg: string; border: string; stroke: string }> =
  {
    pine: {
      text: 'text-pine',
      bg: 'bg-pine-soft',
      border: 'border-pine/35',
      stroke: 'var(--color-pine)',
    },
    ochre: {
      text: 'text-ochre',
      bg: 'bg-ochre-soft',
      border: 'border-ochre/35',
      stroke: 'var(--color-ochre)',
    },
    oxblood: {
      text: 'text-oxblood',
      bg: 'bg-oxblood-soft',
      border: 'border-oxblood/35',
      stroke: 'var(--color-oxblood)',
    },
    slate: {
      text: 'text-slate',
      bg: 'bg-bar',
      border: 'border-rule',
      stroke: 'var(--color-slate)',
    },
  }
