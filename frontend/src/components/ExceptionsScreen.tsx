import { useCallback, useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Sparkles } from 'lucide-react'
import type { ExceptionPage, ExceptionRow, Summary } from '../lib/api'
import { api } from '../lib/api'
import {
  confidenceTone,
  hasVerdict,
  humanise,
  kindTone,
  money,
  toneClasses,
} from '../lib/format'
import { Meter, Pulse, Tag } from './bits'
import { ExceptionDetail } from './ExceptionDetail'

const PAGE_SIZE = 20

const STATUS_TONE = {
  pending: 'slate',
  approved: 'pine',
  rejected: 'oxblood',
  investigating: 'ochre',
} as const

export function ExceptionsScreen({
  runId,
  summary,
  onDecisions,
}: {
  runId: string
  summary: Summary
  onDecisions: (d: Record<string, number>) => void
}) {
  const [page, setPage] = useState(1)
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [action, setAction] = useState('')
  const [maxConfidence, setMaxConfidence] = useState('')
  const [data, setData] = useState<ExceptionPage | null>(null)
  const [selected, setSelected] = useState<ExceptionRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await api.exceptions(runId, {
        page,
        page_size: PAGE_SIZE,
        kind: kind || undefined,
        status: status || undefined,
        action: action || undefined,
        max_confidence: maxConfidence || undefined,
      })
      setData(d)
      setError(null)
      setSelected((prev) =>
        prev ? (d.items.find((i) => i.exception_id === prev.exception_id) ?? prev) : null,
      )
    } catch (e) {
      setError((e as Error).message)
    }
  }, [runId, page, kind, status, action, maxConfidence])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    setPage(1)
  }, [kind, status, action, maxConfidence])

  const act = async (exceptionId: string, verdict: string) => {
    setBusy(true)
    try {
      const res = await api.act(runId, exceptionId, verdict)
      onDecisions(res.decisions)
      setData((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((i) =>
                i.exception_id === exceptionId ? { ...i, ...res.exception } : i,
              ),
            }
          : prev,
      )
      setSelected((prev) =>
        prev && prev.exception_id === exceptionId ? { ...prev, ...res.exception } : prev,
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const facets = data?.facets
  const decided = useMemo(() => {
    const d = summary.decisions ?? {}
    return (d.approved ?? 0) + (d.rejected ?? 0) + (d.investigating ?? 0)
  }, [summary.decisions])

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-[19px] font-semibold">Exceptions</h2>
          <p className="mt-0.5 max-w-[76ch] text-[12px] leading-snug text-slate">
            Everything the engine would not commit on its own, hardest first. Nothing here
            has been actioned — approving is what actions it.
          </p>
        </div>
        <div className="num text-[12px] text-slate">
          {decided} of {summary.exceptions_total} decided
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap">
        <Select
          value={kind}
          onChange={setKind}
          placeholder="All types"
          options={Object.entries(facets?.kind ?? {}).map(([k, n]) => [k, `${humanise(k)} (${n})`])}
        />
        <Select
          value={action}
          onChange={setAction}
          placeholder="Any suggestion"
          options={Object.entries(facets?.suggested_action ?? {}).map(([k, n]) => [
            k,
            `Suggests ${k} (${n})`,
          ])}
        />
        <Select
          value={status}
          onChange={setStatus}
          placeholder="Any state"
          options={Object.entries(facets?.status ?? {}).map(([k, n]) => [
            k,
            `${humanise(k)} (${n})`,
          ])}
        />
        <Select
          value={maxConfidence}
          onChange={setMaxConfidence}
          placeholder="Any confidence"
          options={[
            ['0.5', 'Below 0.50'],
            ['0.7', 'Below 0.70'],
            ['0.85', 'Below 0.85'],
          ]}
        />
        {(kind || status || action || maxConfidence) && (
          <button
            type="button"
            onClick={() => {
              setKind('')
              setStatus('')
              setAction('')
              setMaxConfidence('')
            }}
            className="justify-self-start text-[12px] text-slate underline decoration-rule underline-offset-2 hover:text-ink"
          >
            Clear
          </button>
        )}
        <span className="num col-span-2 text-[11px] text-mute sm:col-span-1 sm:ml-auto">
          {data ? `${data.total} matching` : ''}
        </span>
      </div>

      {error && (
        <div className="mb-3 rounded-[3px] border border-oxblood/35 bg-oxblood-soft px-3 py-2 text-[12px] text-oxblood">
          {error}
        </div>
      )}

      <div className={`grid gap-4 ${selected ? 'lg:grid-cols-[minmax(0,1fr)_400px]' : ''}`}>
        {/* Below lg the detail is a drawer over the list, so the list keeps the
            full width and does not get squeezed to a column that is not there. */}
        <div className="sheet overflow-hidden">
          {/* Column heads only make sense once the row is actually columnar. */}
          <div className="hidden grid-cols-[108px_1fr_130px_92px] gap-2 border-b border-rule px-3 py-1.5 md:grid">
            <span className="label">Type</span>
            <span className="label">What happened</span>
            <span className="label">Confidence</span>
            <span className="label text-right">State</span>
          </div>
          <div className="label border-b border-rule px-3 py-1.5 md:hidden">
            {data ? `${data.total} exceptions, hardest first` : 'Loading'}
          </div>

          {!data && (
            <div className="space-y-2 p-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Pulse key={i} className="h-11" />
              ))}
            </div>
          )}

          {data && data.items.length === 0 && (
            <p className="px-3 py-8 text-center text-[13px] text-slate">
              Nothing matches those filters.
            </p>
          )}

          <div className="greenbar">
            <AnimatePresence initial={false}>
              {data?.items.map((row, i) => (
                <ExceptionRowView
                  key={row.exception_id}
                  row={row}
                  index={i}
                  active={selected?.exception_id === row.exception_id}
                  onOpen={() => setSelected(row)}
                  onAct={(v) => act(row.exception_id, v)}
                  busy={busy}
                />
              ))}
            </AnimatePresence>
          </div>

          {data && data.pages > 1 && (
            <div className="flex items-center justify-between border-t border-rule px-3 py-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="flex items-center gap-1 text-[12px] disabled:opacity-30"
              >
                <ChevronLeft size={13} /> Previous
              </button>
              <span className="num text-[11px] text-slate">
                Page {data.page} of {data.pages}
              </span>
              <button
                type="button"
                disabled={page >= data.pages}
                onClick={() => setPage((p) => p + 1)}
                className="flex items-center gap-1 text-[12px] disabled:opacity-30"
              >
                Next <ChevronRight size={13} />
              </button>
            </div>
          )}
        </div>

        <AnimatePresence>
          {selected && (
            <ExceptionDetail
              key={selected.exception_id}
              exception={selected}
              busy={busy}
              onClose={() => setSelected(null)}
              onAct={(v) => act(selected.exception_id, v)}
            />
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

function ExceptionRowView({
  row,
  index,
  active,
  onOpen,
  onAct,
  busy,
}: {
  row: ExceptionRow
  index: number
  active: boolean
  onOpen: () => void
  onAct: (verdict: string) => void
  busy: boolean
}) {
  const usedLlm = hasVerdict(row.llm?.source)
  const isMock = row.llm?.source === 'mock'
  const confidence = usedLlm ? row.llm!.confidence : row.engine_confidence
  const tone = confidenceTone(confidence)
  const explanation = usedLlm ? row.llm!.explanation : row.engine_note
  const decided = row.status !== 'pending'

  const amount =
    (row.ledger_records?.reduce((a, r) => a + Math.abs(r.amount), 0) ?? 0) ||
    (row.bank_records?.reduce((a, r) => a + Math.abs(r.amount), 0) ?? 0)

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.24, delay: Math.min(0.2, index * 0.022) }}
      className={`grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-2 border-l-2 px-3 py-2.5 transition-colors md:grid-cols-[108px_1fr_130px_92px] md:items-start md:gap-2 md:py-2 ${
        active ? 'bg-ochre-soft/60' : ''
      }`}
      style={{
        borderLeftColor: decided
          ? toneClasses[STATUS_TONE[row.status]].stroke
          : 'transparent',
      }}
    >
      <div className="flex items-center gap-2 pt-[1px] md:block">
        <Tag tone={kindTone(row.kind)}>{humanise(row.kind)}</Tag>
        <div className="num text-[9px] text-mute md:mt-1">{row.exception_id}</div>
      </div>

      {/* On mobile the state/actions column rides up beside the type tag. */}
      <div className="row-start-1 flex flex-col items-end gap-1 md:col-start-4 md:row-auto">
        {decided ? (
          <motion.div
            initial={{ scale: 0.85, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 420, damping: 24 }}
          >
            <Tag tone={STATUS_TONE[row.status]}>{row.status}</Tag>
          </motion.div>
        ) : (
          <div className="flex gap-1">
            <MiniBtn label="✓" title="Approve" id={row.exception_id} tone="pine"
                     onClick={() => onAct('approve')} busy={busy} />
            <MiniBtn label="✕" title="Reject" id={row.exception_id} tone="oxblood"
                     onClick={() => onAct('reject')} busy={busy} />
            <MiniBtn label="?" title="Investigate" id={row.exception_id} tone="ochre"
                     onClick={() => onAct('investigate')} busy={busy} />
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onOpen}
        className="col-span-2 min-w-0 text-left md:col-span-1 md:col-start-2 md:row-start-1"
      >
        <p className="line-clamp-2 text-[12px] leading-snug">{explanation}</p>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
          {amount > 0 && <span className="num text-[11px]">{money(amount)}</span>}
          <span className="num text-[10px] text-mute">
            {[...row.ledger_ids, ...row.stmt_ids].join(' · ')}
          </span>
          {usedLlm ? (
            <span
              className={`label flex items-center gap-1 ${isMock ? 'text-ochre' : 'text-pine'}`}
              title={isMock ? 'Rule-based stand-in, not a live model call' : undefined}
            >
              <Sparkles size={9} /> {humanise(row.llm!.category)}
              {isMock && <span className="text-mute">· stand-in</span>}
            </span>
          ) : row.needs_llm ? (
            <span className="label text-ochre">model unavailable</span>
          ) : (
            <span className="label text-mute">rule only</span>
          )}
        </div>
      </button>

      <div className="col-span-2 flex items-center gap-3 md:col-span-1 md:col-start-3 md:row-start-1 md:block md:pt-[2px]">
        <Meter value={confidence} tone={tone} />
        {row.llm?.suggested_action && usedLlm && (
          <div className="label md:mt-1">suggests {row.llm.suggested_action}</div>
        )}
      </div>
    </motion.div>
  )
}

function MiniBtn({
  label,
  title,
  id,
  tone,
  onClick,
  busy,
}: {
  label: string
  title: string
  /** Every row carries the same three verbs, so the exception id has to be part
   *  of the accessible name or a screen reader hears "Investigate" 20 times. */
  id: string
  tone: 'pine' | 'oxblood' | 'ochre'
  onClick: () => void
  busy: boolean
}) {
  const styles = {
    pine: 'border-pine/35 text-pine hover:bg-pine hover:text-sheet',
    oxblood: 'border-oxblood/35 text-oxblood hover:bg-oxblood hover:text-sheet',
    ochre: 'border-ochre/35 text-ochre hover:bg-ochre hover:text-sheet',
  }
  return (
    <button
      type="button"
      title={`${title} ${id}`}
      aria-label={`${title} ${id}`}
      onClick={onClick}
      disabled={busy}
      className={`h-6 w-6 rounded-[2px] border bg-sheet text-[11px] leading-none transition-colors disabled:opacity-40 ${styles[tone]}`}
    >
      {label}
    </button>
  )
}

function Select({
  value,
  onChange,
  placeholder,
  options,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
  options: [string, string][]
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-[3px] border border-rule bg-sheet px-2 py-1 text-[12px] text-ink transition-colors hover:border-mute focus:border-ink focus:outline-none"
    >
      <option value="">{placeholder}</option>
      {options.map(([v, label]) => (
        <option key={v} value={v}>
          {label}
        </option>
      ))}
    </select>
  )
}
