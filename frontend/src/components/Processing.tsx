import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, Cpu, Loader2 } from 'lucide-react'
import type { BankRecord, LedgerRecord, Link, PassStat, Summary } from '../lib/api'
import { CanvasLegend, MatchCanvas } from './MatchCanvas'

/* ==========================================================================
   The processing sequence.

   The engine finishes in about 25 milliseconds, which is too fast to see and
   too fast to explain. So the stages are paced out - but every number on this
   screen is the real measurement from the run that just happened, including
   the actual per-pass duration, which is printed next to each stage precisely
   so the pacing can't be mistaken for the timing.
   ========================================================================== */

const STAGE_MS = 780
const LLM_MIN_MS = 900

type Props = {
  summary: Summary
  ledger: LedgerRecord[]
  bank: BankRecord[]
  links: Link[]
  llmPhase: 'idle' | 'running' | 'done'
  llmSummary: Summary | null
  onFinished: () => void
}

export function Processing({
  summary,
  ledger,
  bank,
  links,
  llmPhase,
  llmSummary,
  onFinished,
}: Props) {
  const passes = summary.passes
  const [stage, setStage] = useState(0)
  const [llmShown, setLlmShown] = useState(false)
  const doneRef = useRef(false)

  // Walk the deterministic passes.
  useEffect(() => {
    if (stage >= passes.length) return
    const t = setTimeout(() => setStage((s) => s + 1), STAGE_MS)
    return () => clearTimeout(t)
  }, [stage, passes.length])

  // Then hold on the LLM stage until it has genuinely come back.
  useEffect(() => {
    if (stage < passes.length) return
    const t = setTimeout(() => setLlmShown(true), 250)
    return () => clearTimeout(t)
  }, [stage, passes.length])

  useEffect(() => {
    if (!llmShown || llmPhase !== 'done' || doneRef.current) return
    doneRef.current = true
    const t = setTimeout(onFinished, LLM_MIN_MS)
    return () => clearTimeout(t)
  }, [llmShown, llmPhase, onFinished])

  const visiblePasses = new Set(passes.slice(0, stage).map((p) => p.name))

  return (
    <div className="grid gap-4 px-4 py-6 sm:px-6 lg:grid-cols-[380px_minmax(0,1fr)]">
      <div>
        <div className="label mb-2">Passes, in the order they ran</div>
        <ol className="sheet divide-y divide-rule">
          {passes.map((p, i) => (
            <PassRow key={p.name} pass={p} state={i < stage ? 'done' : i === stage ? 'live' : 'waiting'} />
          ))}
          <LlmRow
            shown={llmShown}
            phase={llmPhase}
            requested={summary.exceptions_needing_llm}
            stats={llmSummary?.llm_stats ?? null}
          />
        </ol>

        <p className="mt-3 text-[11px] leading-relaxed text-slate">
          Stages are paced so they can be read. The millisecond figure beside each one is
          the real time that pass took — the whole deterministic run finished in{' '}
          <span className="num">
            {passes.reduce((a, p) => a + p.duration_ms, 0).toFixed(0)} ms
          </span>
          .
        </p>
      </div>

      <div className="sheet overflow-hidden">
        <MatchCanvas
          ledger={ledger}
          bank={bank}
          links={links}
          visiblePasses={visiblePasses}
          height={520}
        />
        <div className="border-t border-rule">
          <CanvasLegend />
        </div>
      </div>
    </div>
  )
}

function PassRow({ pass, state }: { pass: PassStat; state: 'done' | 'live' | 'waiting' }) {
  return (
    <li
      className={`flex items-start gap-2.5 px-3 py-2 transition-opacity ${
        state === 'waiting' ? 'opacity-35' : 'opacity-100'
      }`}
    >
      <span className="mt-[3px] flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        {state === 'done' ? (
          <motion.span
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: 'spring', stiffness: 500, damping: 22 }}
          >
            <Check size={13} className="text-pine" />
          </motion.span>
        ) : state === 'live' ? (
          <Loader2 size={13} className="animate-spin text-ochre" />
        ) : (
          <span className="h-1.5 w-1.5 rounded-full bg-rule" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[13px] font-medium">{pass.label}</span>
          <span className="num text-[10px] text-mute">{pass.duration_ms.toFixed(1)} ms</span>
        </div>
        <p className="text-[11px] leading-snug text-slate">{pass.description}</p>

        <AnimatePresence>
          {state === 'done' && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              transition={{ duration: 0.25 }}
              className="num mt-1 flex gap-3 overflow-hidden text-[10px]"
            >
              <span className={pass.links_made ? 'text-pine' : 'text-mute'}>
                +{pass.links_made} links
              </span>
              {pass.exceptions_raised > 0 && (
                <span className="text-ochre">+{pass.exceptions_raised} exceptions</span>
              )}
              <span className="text-mute">
                {pass.remaining_ledger}L / {pass.remaining_bank}B left
              </span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </li>
  )
}

function LlmRow({
  shown,
  phase,
  requested,
  stats,
}: {
  shown: boolean
  phase: 'idle' | 'running' | 'done'
  requested: number
  stats: Summary['llm_stats']
}) {
  const unavailable = phase === 'done' && stats && stats.answered === 0 && stats.requested > 0

  return (
    <li className={`flex items-start gap-2.5 px-3 py-2 transition-opacity ${shown ? '' : 'opacity-35'}`}>
      <span className="mt-[3px] flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        {phase === 'done' && shown ? (
          <Check size={13} className={unavailable ? 'text-ochre' : 'text-pine'} />
        ) : shown ? (
          <Loader2 size={13} className="animate-spin text-ochre" />
        ) : (
          <Cpu size={12} className="text-rule" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[13px] font-medium">Language model</span>
          <span className="num text-[10px] text-mute">
            {requested} of the batch
          </span>
        </div>
        <p className="text-[11px] leading-snug text-slate">
          Only what six passes could not settle. It classifies and explains; it never resolves.
        </p>
        <AnimatePresence>
          {phase === 'done' && shown && stats && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              className="num mt-1 flex flex-wrap gap-3 overflow-hidden text-[10px]"
            >
              {unavailable ? (
                <span className="text-ochre">no key — explanations marked unavailable</span>
              ) : (
                <>
                  <span className="text-pine">{stats.answered} explained</span>
                  <span className="text-mute">{stats.api_calls} api calls</span>
                  {stats.from_cache > 0 && (
                    <span className="text-mute">{stats.from_cache} cached</span>
                  )}
                  {stats.model && <span className="text-mute">{stats.model}</span>}
                </>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </li>
  )
}
