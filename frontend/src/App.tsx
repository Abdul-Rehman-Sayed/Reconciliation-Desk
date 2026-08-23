import { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { RotateCcw } from 'lucide-react'
import type {
  Accuracy,
  BankRecord,
  Health,
  LedgerRecord,
  Link,
  Summary,
} from './lib/api'
import { api } from './lib/api'
import { pct } from './lib/format'
import { LandingScreen } from './components/LandingScreen'
import { StartScreen } from './components/StartScreen'
import { Processing } from './components/Processing'
import { SummaryScreen } from './components/SummaryScreen'
import { ExceptionsScreen } from './components/ExceptionsScreen'
import { EvidenceScreen } from './components/EvidenceScreen'

type Screen = 'landing' | 'start' | 'processing' | 'summary' | 'evidence' | 'exceptions'

/** One source of truth for the flow. The rail and the mobile bar both read it. */
const STEPS: [Screen, string][] = [
  ['start', 'Load'],
  ['processing', 'Match'],
  ['summary', 'Result'],
  ['evidence', 'Evidence'],
  ['exceptions', 'Review'],
]
type StartMode =
  | { kind: 'bundled'; dataset: string }
  | { kind: 'upload'; ledger: File; bank: File }

export default function App() {
  const [screen, setScreen] = useState<Screen>('landing')
  const [runId, setRunId] = useState<string | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [accuracy, setAccuracy] = useState<Accuracy | null>(null)
  const [ledger, setLedger] = useState<LedgerRecord[]>([])
  const [bank, setBank] = useState<BankRecord[]>([])
  const [links, setLinks] = useState<Link[]>([])
  const [llmPhase, setLlmPhase] = useState<'idle' | 'running' | 'done'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [reused, setReused] = useState(false)

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined)
  }, [])

  const start = useCallback(async (mode: StartMode) => {
    setError(null)
    setLlmPhase('idle')
    try {
      const res =
        mode.kind === 'bundled'
          ? await api.reconcileBundled(mode.dataset)
          : await api.reconcileUpload(mode.ledger, mode.bank)

      const id = res.run_id
      const recs = await api.records(id)
      const full = await api.summary(id)

      setRunId(id)
      setSummary(full)
      setAccuracy(res.accuracy)
      setLedger(recs.ledger)
      setBank(recs.bank)
      setLinks(recs.links)
      setReused(res.reused)
      setScreen('processing')

      // The model runs only after the deterministic passes have all landed.
      // If this run was reused, its verdicts came back with it and explain()
      // short-circuits on already_done - so a repeat run costs nothing at all.
      setLlmPhase('running')
      try {
        await api.explain(id)
      } catch {
        /* explain() already degrades honestly; the summary refresh shows it */
      }
      const after = await api.summary(id)
      setSummary(after)
      setLlmPhase('done')
    } catch (e) {
      setError((e as Error).message)
      setScreen('start')
    }
  }, [])

  const reset = () => {
    setScreen('start')
    setRunId(null)
    setSummary(null)
    setAccuracy(null)
    setLedger([])
    setBank([])
    setLinks([])
    setLlmPhase('idle')
    setReused(false)
  }

  /** A threshold change commits a derived run; swap to it in place. */
  const adoptRun = useCallback(async (id: string) => {
    const [full, recs] = await Promise.all([api.summary(id), api.records(id)])
    setRunId(id)
    setSummary(full)
    setLedger(recs.ledger)
    setBank(recs.bank)
    setLinks(recs.links)
    try {
      setAccuracy(await api.accuracy(id))
    } catch {
      setAccuracy(null)
    }
  }, [])

  // The landing page sits in front of the app rather than inside it, so it gets
  // the window to itself - no rail, no step bar, no status strip. It is also
  // deliberately absent from STEPS: it is not a stage of the run, and showing it
  // as step zero would imply you can navigate back to it mid-reconciliation and
  // still have one.
  if (screen === 'landing') {
    return <LandingScreen onEnter={() => setScreen('start')} />
  }

  return (
    <div className="flex min-h-screen">
      <Rail
        screen={screen}
        summary={summary}
        runId={runId}
        onNavigate={(s) => summary && setScreen(s)}
        onReset={reset}
        onHome={() => setScreen('landing')}
      />

      <main className="min-w-0 flex-1">
        <StatusStrip
          summary={summary}
          health={health}
          llmPhase={llmPhase}
          reused={reused}
        />

        {/* Below md the rail is gone, so the flow needs somewhere else to live.
            Scrolls horizontally rather than wrapping - five steps on a narrow
            phone would otherwise take two rows and push the content down. */}
        <StepBar
          screen={screen}
          enabled={Boolean(summary)}
          onNavigate={(s) => summary && setScreen(s)}
          onReset={reset}
          runId={runId}
        />

        <AnimatePresence mode="wait">
          <motion.div
            key={screen}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            {screen === 'start' && <StartScreen onStart={start} error={error} />}

            {screen === 'processing' && summary && (
              <Processing
                summary={summary}
                ledger={ledger}
                bank={bank}
                links={links}
                llmPhase={llmPhase}
                llmSummary={summary}
                onFinished={() => setScreen('summary')}
              />
            )}

            {screen === 'summary' && summary && (
              <SummaryScreen
                summary={summary}
                accuracy={accuracy}
                ledger={ledger}
                bank={bank}
                links={links}
                runId={runId}
                onReview={() => setScreen('exceptions')}
                onEvidence={() => setScreen('evidence')}
              />
            )}

            {screen === 'evidence' && summary && runId && (
              <EvidenceScreen
                runId={runId}
                summary={summary}
                onRunChange={(id) => void adoptRun(id)}
              />
            )}

            {screen === 'exceptions' && summary && runId && (
              <ExceptionsScreen
                runId={runId}
                summary={summary}
                onDecisions={(decisions) =>
                  setSummary((s) => (s ? { ...s, decisions } : s))
                }
              />
            )}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}

/* ------------------------------------------------------------------ rail */
function Rail({
  screen,
  summary,
  runId,
  onNavigate,
  onReset,
  onHome,
}: {
  screen: Screen
  summary: Summary | null
  runId: string | null
  onNavigate: (s: Screen) => void
  onReset: () => void
  onHome: () => void
}) {
  const current = STEPS.findIndex(([s]) => s === screen)

  return (
    <aside className="sticky top-0 hidden h-screen w-[212px] shrink-0 flex-col border-r border-rule bg-sheet md:flex">
      <div className="border-b border-rule px-4 py-4">
        <button
          type="button"
          onClick={onHome}
          className="flex items-center gap-2 text-left transition-opacity hover:opacity-70"
          title="Back to the overview"
        >
          <Mark />
          <div>
            <div className="text-[13px] font-semibold leading-tight">Reconciliation</div>
            <div className="label" style={{ letterSpacing: '0.13em' }}>
              Desk
            </div>
          </div>
        </button>
      </div>

      <nav className="px-2 py-3">
        {STEPS.map(([s, label], i) => {
          const state = i < current ? 'past' : i === current ? 'now' : 'ahead'
          const clickable = Boolean(summary) && s !== 'processing'
          return (
            <button
              key={s}
              type="button"
              disabled={!clickable}
              onClick={() => onNavigate(s)}
              className={`flex w-full items-center gap-2.5 rounded-[3px] px-2 py-1.5 text-left transition-colors ${
                state === 'now' ? 'bg-field' : 'hover:bg-field/60'
              } disabled:cursor-default disabled:hover:bg-transparent`}
            >
              <span
                className={`num flex h-4 w-4 items-center justify-center rounded-full text-[9px] ${
                  state === 'ahead'
                    ? 'bg-bar text-mute'
                    : state === 'now'
                      ? 'bg-ink text-sheet'
                      : 'bg-pine text-sheet'
                }`}
              >
                {i + 1}
              </span>
              <span
                className={`text-[13px] ${
                  state === 'ahead' ? 'text-mute' : state === 'now' ? 'font-medium' : ''
                }`}
              >
                {label}
              </span>
            </button>
          )
        })}
      </nav>

      {summary && (
        <div className="border-t border-rule px-4 py-3">
          <div className="label mb-1.5">This run</div>
          <dl className="num space-y-1 text-[11px]">
            <RailRow k="dataset" v={summary.dataset_profile} />
            <RailRow k="records" v={String(summary.total_records)} />
            <RailRow k="auto" v={pct(summary.match_rate_auto, 1)} tone="text-pine" />
            <RailRow
              k="queue"
              v={String(summary.exceptions_total)}
              tone="text-ochre"
            />
            <RailRow
              k="open"
              v={String(summary.records_unresolved)}
              tone={summary.records_unresolved ? 'text-oxblood' : 'text-slate'}
            />
          </dl>
        </div>
      )}

      <div className="mt-auto border-t border-rule px-4 py-3">
        {runId && (
          <div className="num mb-2 truncate text-[9px] text-mute" title={runId}>
            {runId}
          </div>
        )}
        <button
          type="button"
          onClick={onReset}
          className="flex items-center gap-1.5 text-[12px] text-slate transition-colors hover:text-ink"
        >
          <RotateCcw size={12} /> New run
        </button>
      </div>
    </aside>
  )
}

/* ------------------------------------------------------- mobile step bar */
function StepBar({
  screen,
  enabled,
  onNavigate,
  onReset,
  runId,
}: {
  screen: Screen
  enabled: boolean
  onNavigate: (s: Screen) => void
  onReset: () => void
  runId: string | null
}) {
  const current = STEPS.findIndex(([s]) => s === screen)

  return (
    <nav
      aria-label="Progress"
      className="sticky top-[29px] z-10 flex items-center gap-1 overflow-x-auto border-b border-rule bg-sheet px-3 py-1.5 md:hidden"
    >
      <Mark />
      {STEPS.map(([s, label], i) => {
        const state = i < current ? 'past' : i === current ? 'now' : 'ahead'
        const clickable = enabled && s !== 'processing'
        return (
          <button
            key={s}
            type="button"
            disabled={!clickable}
            aria-current={state === 'now' ? 'step' : undefined}
            onClick={() => onNavigate(s)}
            className={`flex shrink-0 items-center gap-1.5 rounded-[3px] px-2 py-1 text-[12px] transition-colors ${
              state === 'now' ? 'bg-field font-medium' : 'text-slate'
            } disabled:opacity-40`}
          >
            <span
              className={`num flex h-4 w-4 items-center justify-center rounded-full text-[9px] ${
                state === 'ahead'
                  ? 'bg-bar text-mute'
                  : state === 'now'
                    ? 'bg-ink text-sheet'
                    : 'bg-pine text-sheet'
              }`}
            >
              {i + 1}
            </span>
            {label}
          </button>
        )
      })}
      {runId && (
        <button
          type="button"
          onClick={onReset}
          className="ml-auto flex shrink-0 items-center gap-1 pl-2 text-[12px] text-slate transition-colors hover:text-ink"
        >
          <RotateCcw size={11} /> New
        </button>
      )}
    </nav>
  )
}

function RailRow({ k, v, tone = 'text-ink' }: { k: string; v: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-mute">{k}</dt>
      <dd className={`truncate ${tone}`}>{v}</dd>
    </div>
  )
}

/** Two columns, one rule between them. The whole problem in nine strokes. */
function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3 6h6M3 12h6M3 18h6M15 6h6M15 12h6M15 18h6"
        stroke="var(--color-pine)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path d="M12 4v16" stroke="var(--color-rule)" strokeWidth="1" />
    </svg>
  )
}

/* ---------------------------------------------------------- status strip */
function StatusStrip({
  summary,
  health,
  llmPhase,
  reused,
}: {
  summary: Summary | null
  health: Health | null
  llmPhase: 'idle' | 'running' | 'done'
  reused: boolean
}) {
  const stats = summary?.llm_stats
  const mock = health?.mock_mode || stats?.mode === 'mock'
  // Which of these is true changes what the reader should trust, so the strip
  // says which one rather than showing a generic green dot.
  const modelLabel = mock
    ? 'stand-in · no model called'
    : health?.groq_reachable
      ? `groq · ${health.groq_model ?? 'ready'}${llmPhase === 'running' ? ' · calling' : ''}`
      : 'groq · no key'
  // Exceptions the model was asked about and did not answer. These carry a
  // real engine finding and still need a person, so they are not a silent
  // subset - but nothing else on this screen distinguishes them from a verdict
  // the model actually returned, which is exactly how a run where Groq answered
  // 1 of 66 once read as a clean pass.
  const unanswered = stats ? Math.max(0, stats.requested - stats.answered) : 0
  const degraded = !mock && llmPhase === 'done' && unanswered > 0
  const tone = mock || degraded
    ? 'var(--color-ochre)'
    : health?.groq_reachable
      ? 'var(--color-pine)'
      : 'var(--color-ochre)'

  return (
    <div className="sticky top-0 z-20 flex items-center gap-x-5 gap-y-1 overflow-x-auto border-b border-rule bg-field/95 px-4 py-1.5 backdrop-blur sm:px-6">
      <Readout k="engine" v="deterministic · 6 passes" />
      {summary && (
        <>
          <Readout k="records" v={String(summary.total_records)} />
          <Readout
            k="auto"
            v={pct(summary.match_rate_auto, 2)}
            tone="text-pine"
          />
          <Readout k="queue" v={String(summary.exceptions_total)} tone="text-ochre" />
        </>
      )}
      {reused && (
        <Readout k="run" v="reused · 0 new calls" tone="text-pine" />
      )}
      {stats && stats.from_cache > 0 && (
        <Readout
          k="cached"
          v={`${stats.from_cache}/${stats.requested}`}
          tone="text-pine"
        />
      )}
      {degraded && (
        <Readout
          k="unexplained"
          v={`${unanswered}/${stats?.requested ?? 0}`}
          tone="text-ochre"
        />
      )}
      <div className="ml-auto flex shrink-0 items-center gap-1.5 pl-3">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: tone }} />
        <span className="num whitespace-nowrap text-[10px] text-slate">{modelLabel}</span>
      </div>
    </div>
  )
}

function Readout({ k, v, tone = 'text-ink' }: { k: string; v: string; tone?: string }) {
  return (
    <span className="flex shrink-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className="label">{k}</span>
      <span className={`num text-[11px] ${tone}`}>{v}</span>
    </span>
  )
}
