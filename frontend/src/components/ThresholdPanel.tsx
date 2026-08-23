import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Check, RotateCcw, ShieldCheck, Zap } from 'lucide-react'
import type { Summary, ThresholdInfo, ThresholdPreview, ThresholdSpec } from '../lib/api'
import { api } from '../lib/api'
import { pct } from '../lib/format'
import { Panel } from './bits'

/* ==========================================================================
   Live tolerance adjustment.

   The one screen here that touches the risky part of the system, so it is
   built to make the risk impossible rather than unlikely:

   - Moving a slider calls /thresholds, which re-runs the six deterministic
     passes and nothing else. That route cannot reach the model. It is not
     "we chose not to call it" - there is no code path from a slider to Groq.
   - Every preview reports LLM coverage: how many exceptions at these new
     settings already have a cached verdict and how many would need a fresh
     call. That number is shown before anything could be spent.
   - Explaining a new setting is a separate, deliberate button.

   This matters because the person dragging the slider during a demo is an
   audience member, and an audience member should not be able to spend quota.
   ========================================================================== */

const DEBOUNCE_MS = 180

type Props = {
  runId: string
  summary: Summary
  onCommitted: (runId: string) => void
}

export function ThresholdPanel({ runId, summary, onCommitted }: Props) {
  const [info, setInfo] = useState<ThresholdInfo | null>(null)
  const [values, setValues] = useState<Record<string, number>>({})
  const [preview, setPreview] = useState<ThresholdPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [committed, setCommitted] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    api
      .thresholds()
      .then((d) => {
        setInfo(d)
        const start: Record<string, number> = {}
        for (const spec of d.adjustable) start[spec.key] = Number(d.defaults[spec.key])
        setValues(start)
      })
      .catch((e) => setError((e as Error).message))
  }, [])

  const dirty =
    info != null &&
    info.adjustable.some((s) => values[s.key] !== Number(info.defaults[s.key]))

  const run = useCallback(
    (next: Record<string, number>) => {
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(async () => {
        setBusy(true)
        try {
          setPreview(await api.previewThresholds(runId, next))
          setError(null)
        } catch (e) {
          setError((e as Error).message)
        } finally {
          setBusy(false)
        }
      }, DEBOUNCE_MS)
    },
    [runId],
  )

  const move = (key: string, value: number) => {
    const next = { ...values, [key]: value }
    setValues(next)
    setCommitted(false)
    run(next)
  }

  const reset = () => {
    if (!info) return
    const start: Record<string, number> = {}
    for (const spec of info.adjustable) start[spec.key] = Number(info.defaults[spec.key])
    setValues(start)
    setPreview(null)
    setCommitted(false)
    window.clearTimeout(timer.current)
  }

  const commit = async () => {
    setBusy(true)
    try {
      const result = await api.commitThresholds(runId, values)
      setPreview(result)
      setCommitted(true)
      if (result.run_id) onCommitted(result.run_id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!info) return null

  const shown = preview?.summary ?? summary
  const delta = preview?.delta
  const coverage = preview?.llm_coverage

  return (
    <Panel
      title="Move the tolerances"
      className="mt-4"
      right={
        <span className="label flex items-center gap-1 text-pine">
          <ShieldCheck size={11} /> deterministic · no model call
        </span>
      }
    >
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="divide-y divide-rule">
          {info.adjustable.map((spec) => (
            <Slider
              key={spec.key}
              spec={spec}
              value={values[spec.key] ?? Number(info.defaults[spec.key])}
              base={Number(info.defaults[spec.key])}
              onChange={(v) => move(spec.key, v)}
            />
          ))}
        </div>

        <aside className="border-t border-rule bg-field/40 lg:border-l lg:border-t-0">
          <div className="px-3 py-3">
            <div className="label mb-1">Resolved by rule</div>
            <div className="num flex items-baseline gap-2">
              <span className="text-[34px] font-medium leading-none text-pine">
                {pct(shown.match_rate_auto, 2)}
              </span>
              {delta && delta.match_rate_auto !== 0 && (
                <motion.span
                  key={delta.match_rate_auto}
                  initial={{ opacity: 0, y: -3 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`text-[13px] ${
                    delta.match_rate_auto > 0 ? 'text-pine' : 'text-oxblood'
                  }`}
                >
                  {delta.match_rate_auto > 0 ? '+' : ''}
                  {(delta.match_rate_auto * 100).toFixed(2)}
                </motion.span>
              )}
            </div>
            <p className="num mt-1 text-[11px] text-slate">
              {shown.records_auto_resolved} of {shown.total_records} records
              {busy && <span className="ml-1.5 text-mute">recomputing…</span>}
            </p>
          </div>

          <dl className="num divide-y divide-rule border-t border-rule text-[11px]">
            <Line
              k="exceptions"
              v={shown.exceptions_total}
              d={delta?.exceptions_total}
              invert
            />
            <Line k="proposed" v={shown.records_proposed} d={delta?.records_proposed} invert />
            <Line
              k="unresolved"
              v={shown.records_unresolved}
              d={delta?.records_unresolved}
              invert
            />
            {preview?.accuracy && (
              <>
                <Line
                  k="precision"
                  v={pct(preview.accuracy.precision, 2)}
                  d={delta?.precision}
                  asPct
                />
                <Line k="recall" v={pct(preview.accuracy.recall, 2)} d={delta?.recall} asPct />
                <Line
                  k="auto precision"
                  v={pct(preview.accuracy.auto_precision, 2)}
                  d={delta?.auto_precision}
                  asPct
                />
              </>
            )}
            {preview && (
              <div className="flex justify-between gap-2 px-3 py-1.5">
                <dt className="text-mute">engine time</dt>
                <dd>{preview.engine_ms.toFixed(0)} ms</dd>
              </div>
            )}
          </dl>

          {coverage && (
            <div className="border-t border-rule px-3 py-2.5">
              <div className="label mb-1 flex items-center gap-1 text-pine">
                <Zap size={10} /> What explaining this would cost
              </div>
              <p className="num text-[11px] text-ink">
                {coverage.already_cached} of {coverage.exceptions_needing_model} already
                cached
                {coverage.would_need_new_calls > 0 ? (
                  <>
                    {' · '}
                    <span className="text-ochre">
                      {coverage.would_need_new_calls} new ({coverage.would_cost_requests}{' '}
                      request
                      {coverage.would_cost_requests === 1 ? '' : 's'})
                    </span>
                  </>
                ) : (
                  <span className="text-pine"> · nothing new to ask</span>
                )}
              </p>
              <p className="mt-1 text-[10px] leading-snug text-mute">{coverage.note}</p>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-rule px-3 py-2.5">
            <button
              type="button"
              onClick={commit}
              disabled={!dirty || busy || committed}
              className="inline-flex items-center gap-1.5 rounded-[3px] bg-ink px-2.5 py-1.5 text-[12px] font-medium text-sheet transition-colors hover:bg-pine disabled:opacity-30"
            >
              {committed ? <Check size={12} /> : null}
              {committed ? 'Kept' : 'Keep this setting'}
            </button>
            <button
              type="button"
              onClick={reset}
              disabled={!dirty || busy}
              className="inline-flex items-center gap-1.5 text-[12px] text-slate transition-colors hover:text-ink disabled:opacity-30"
            >
              <RotateCcw size={11} /> Back to defaults
            </button>
          </div>
        </aside>
      </div>

      {error && (
        <p className="border-t border-rule bg-oxblood-soft px-3 py-2 text-[12px] text-oxblood">
          {error}
        </p>
      )}

      <p className="border-t border-rule px-3 py-2 text-[11px] leading-relaxed text-slate">
        {info.note} Watch precision as you loosen: pushing the reference threshold down buys
        match rate and starts paying for it in wrong links, which is the trade the whole
        design exists to keep visible.
      </p>
    </Panel>
  )
}

function Slider({
  spec,
  value,
  base,
  onChange,
}: {
  spec: ThresholdSpec
  value: number
  base: number
  onChange: (v: number) => void
}) {
  const moved = value !== base
  const display =
    spec.display === 'percent'
      ? `${(value * 100).toFixed(2)}%`
      : spec.unit === 'INR'
        ? `₹${value.toFixed(2)}`
        : spec.unit === 'days'
          ? `${value} d`
          : value.toFixed(2)

  return (
    <div className="px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={spec.key} className="text-[12px] font-medium">
          {spec.label}
        </label>
        <span className={`num text-[12px] ${moved ? 'font-medium text-ochre' : 'text-slate'}`}>
          {display}
          {moved && (
            <span className="ml-1.5 text-[10px] text-mute">
              was{' '}
              {spec.display === 'percent'
                ? `${(base * 100).toFixed(2)}%`
                : base}
            </span>
          )}
        </span>
      </div>
      <input
        id={spec.key}
        type="range"
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1.5 w-full accent-[var(--color-pine)]"
        aria-describedby={`${spec.key}-help`}
      />
      <p id={`${spec.key}-help`} className="mt-0.5 text-[11px] leading-snug text-slate">
        {spec.help}
      </p>
    </div>
  )
}

function Line({
  k,
  v,
  d,
  invert,
  asPct,
}: {
  k: string
  v: number | string
  d?: number
  invert?: boolean
  asPct?: boolean
}) {
  // For exceptions and unresolved counts, fewer is better - so the colour of a
  // negative delta has to flip. Getting this backwards would read as the engine
  // improving while it degrades.
  const good = d === undefined || d === 0 ? null : invert ? d < 0 : d > 0
  return (
    <div className="flex justify-between gap-2 px-3 py-1.5">
      <dt className="text-mute">{k}</dt>
      <dd className="flex items-baseline gap-1.5">
        <span>{v}</span>
        {d !== undefined && d !== 0 && (
          <span className={`text-[10px] ${good ? 'text-pine' : 'text-oxblood'}`}>
            {d > 0 ? '+' : ''}
            {asPct ? (d * 100).toFixed(2) : d}
          </span>
        )}
      </dd>
    </div>
  )
}
