import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { AnyRecord, BankRecord, LedgerRecord, Link } from '../lib/api'
import { money, shortDate } from '../lib/format'


const ROW_H = 18

const MIN_GUTTER = 44
const MIN_COL = 116
const COL_SHARE = 0.42

const DATE_MIN_COL = 168

function geometry(width: number) {
  const ideal = width * COL_SHARE
  const widest = (width - MIN_GUTTER) / 2
  const colW = Math.max(MIN_COL, Math.min(ideal, widest))
  return { colW, leftX: colW, rightX: Math.max(colW + 8, width - colW) }
}

const PASS_STROKE: Record<string, string> = {
  exact: 'var(--color-pine)',
  refund: 'var(--color-pine)',
  tolerant: 'var(--color-pine)',
  composite: 'var(--color-ochre)',
  fuzzy: 'var(--color-ochre)',
}

const PASS_OPACITY: Record<string, number> = {
  exact: 0.32,
  refund: 0.65,
  tolerant: 0.5,
  composite: 0.8,
  fuzzy: 0.8,
}

type Props = {
  ledger: LedgerRecord[]
  bank: BankRecord[]
  links: Link[]
  visiblePasses?: Set<string> | null
  height?: number
  selectedIds?: Set<string>
  onSelect?: (record: AnyRecord) => void
  animateLines?: boolean
}

export function MatchCanvas({
  ledger,
  bank,
  links,
  visiblePasses = null,
  height = 520,
  selectedIds,
  onSelect,
  animateLines = true,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(900)

  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const ledgerIndex = useMemo(() => {
    const m = new Map<string, number>()
    ledger.forEach((r, i) => m.set(r.id, i))
    return m
  }, [ledger])

  const bankIndex = useMemo(() => {
    const m = new Map<string, number>()
    bank.forEach((r, i) => m.set(r.id, i))
    return m
  }, [bank])

  const matched = useMemo(() => {
    const auto = new Set<string>()
    const proposed = new Set<string>()
    for (const l of links) {
      if (visiblePasses && !visiblePasses.has(l.pass_name)) continue
      const bucket = l.auto_resolved ? auto : proposed
      l.ledger_ids.forEach((i) => bucket.add(i))
      l.stmt_ids.forEach((i) => bucket.add(i))
    }
    return { auto, proposed }
  }, [links, visiblePasses])

  const contentHeight = Math.max(ledger.length, bank.length) * ROW_H
  const { colW, leftX, rightX } = geometry(width)
  const showDate = colW >= DATE_MIN_COL

  const paths = useMemo(() => {
    const out: { d: string; stroke: string; opacity: number; key: string }[] = []
    for (const link of links) {
      if (visiblePasses && !visiblePasses.has(link.pass_name)) continue
      const stroke = PASS_STROKE[link.pass_name] ?? 'var(--color-slate)'
      const opacity = PASS_OPACITY[link.pass_name] ?? 0.6
      for (const lid of link.ledger_ids) {
        const li = ledgerIndex.get(lid)
        if (li === undefined) continue
        for (const sid of link.stmt_ids) {
          const bi = bankIndex.get(sid)
          if (bi === undefined) continue
          const y1 = li * ROW_H + ROW_H / 2
          const y2 = bi * ROW_H + ROW_H / 2
          const mid = (leftX + rightX) / 2
          out.push({
            key: `${link.link_id}-${lid}-${sid}`,
            d: `M ${leftX} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${rightX} ${y2}`,
            stroke,
            opacity,
          })
        }
      }
    }
    return out
  }, [links, visiblePasses, ledgerIndex, bankIndex, leftX, rightX])

  return (
    <div className="relative">
      <div className="flex items-end justify-between gap-2 border-b border-rule px-3 pb-1.5">
        <div className="label truncate">Ledger · {ledger.length}</div>
        <div className="label hidden shrink-0 sm:block">{paths.length} pairs drawn</div>
        <div className="label truncate text-right">Statement · {bank.length}</div>
      </div>

      <div
        ref={wrapRef}
        className="relative overflow-y-auto overflow-x-hidden"
        style={{ height }}
      >
        <div className="relative" style={{ height: contentHeight }}>
          <svg
            className="pointer-events-none absolute inset-0"
            width={width}
            height={contentHeight}
            aria-hidden="true"
          >
            {paths.map((p, i) => (
              <motion.path
                key={p.key}
                d={p.d}
                fill="none"
                stroke={p.stroke}
                strokeWidth={1}
                strokeOpacity={p.opacity}
                initial={animateLines ? { pathLength: 0 } : false}
                animate={{ pathLength: 1 }}
                transition={
                  animateLines
                    ? { duration: 0.5, delay: Math.min(0.5, i * 0.0016), ease: 'easeOut' }
                    : { duration: 0 }
                }
              />
            ))}
          </svg>

          <div className="absolute left-0 top-0" style={{ width: colW }}>
            {ledger.map((r, i) => (
              <Row
                key={r.id}
                record={r}
                y={i * ROW_H}
                width={colW}
                showDate={showDate}
                align="left"
                state={
                  matched.auto.has(r.id)
                    ? 'auto'
                    : matched.proposed.has(r.id)
                      ? 'proposed'
                      : 'open'
                }
                selected={selectedIds?.has(r.id) ?? false}
                onSelect={onSelect}
              />
            ))}
          </div>

          <div className="absolute right-0 top-0" style={{ width: colW }}>
            {bank.map((r, i) => (
              <Row
                key={r.id}
                record={r}
                y={i * ROW_H}
                width={colW}
                showDate={showDate}
                align="right"
                state={
                  matched.auto.has(r.id)
                    ? 'auto'
                    : matched.proposed.has(r.id)
                      ? 'proposed'
                      : 'open'
                }
                selected={selectedIds?.has(r.id) ?? false}
                onSelect={onSelect}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function Row({
  record,
  y,
  width,
  align,
  state,
  selected,
  showDate,
  onSelect,
}: {
  record: AnyRecord
  y: number
  width: number
  align: 'left' | 'right'
  state: 'auto' | 'proposed' | 'open'
  selected: boolean
  showDate: boolean
  onSelect?: (r: AnyRecord) => void
}) {
  const edge =
    state === 'auto'
      ? 'var(--color-pine)'
      : state === 'proposed'
        ? 'var(--color-ochre)'
        : 'var(--color-oxblood)'

  const dim = state === 'auto' ? 'opacity-55' : ''

  return (
    <button
      type="button"
      onClick={() => onSelect?.(record)}
      title={`${record.id} · ${record.reference_number || 'no reference'}`}
      className={`absolute flex items-center gap-2 px-2 text-left transition-colors focus-visible:z-10 focus-visible:outline focus-visible:outline-1 focus-visible:outline-ink ${dim} ${
        selected ? 'bg-ochre-soft' : 'hover:bg-bar'
      }`}
      style={{
        top: y,
        height: ROW_H,
        width,
        [align === 'left' ? 'borderLeft' : 'borderRight']: `2px solid ${edge}`,
        flexDirection: align === 'left' ? 'row' : 'row-reverse',
      }}
    >
      <span className="num w-[52px] shrink-0 text-[10px] text-mute">{record.id}</span>
      {showDate && (
        <span className="num w-[62px] shrink-0 text-[10px] text-slate">
          {shortDate(record.date)}
        </span>
      )}
      <span
        className="num flex-1 truncate text-[11px]"
        style={{ textAlign: align === 'left' ? 'right' : 'left' }}
      >
        {money(record.amount)}
      </span>
    </button>
  )
}

export function CanvasLegend() {
  const items: [string, string, string][] = [
    ['var(--color-pine)', 'Resolved by rule', 'exact · reversal · tolerant'],
    ['var(--color-ochre)', 'Proposed, awaiting you', 'composite · fuzzy'],
    ['var(--color-oxblood)', 'No counterpart found', 'goes to the exception queue'],
  ]
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 px-3 py-2">
      {items.map(([color, title, sub]) => (
        <div key={title} className="flex min-w-0 items-center gap-1.5">
          <span className="h-[2px] w-4 shrink-0 rounded-full" style={{ background: color }} />
          <span className="text-[11px] text-ink">{title}</span>
          <span className="num hidden truncate text-[10px] text-mute sm:inline">{sub}</span>
        </div>
      ))}
    </div>
  )
}
