import { motion } from 'framer-motion'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useEffect, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { useIsNarrow } from '../lib/useIsNarrow'
import { FlaskConical, ScrollText, ShieldCheck } from 'lucide-react'
import type {
  Accuracy,
  AnyRecord,
  BankRecord,
  CostResponse,
  LedgerRecord,
  Link,
  Summary,
} from '../lib/api'
import { api } from '../lib/api'
import { compactMoney, count, hours, humanise, money, pct } from '../lib/format'
import { CountUp, Panel, Stat, Tag } from './bits'
import { CanvasLegend, MatchCanvas } from './MatchCanvas'
import { ProvenancePanel } from './ProvenancePanel'

/* Chart-only status fills, re-stepped from the same hues as the text tokens.
   The ink colours are tuned for type on a pale sheet and read too gray as
   fills; these are validated for lightness, chroma, CVD separation and
   normal-vision separation against the #F7F9F5 sheet. */
const FILL = {
  correct: '#0F7A5A',
  missed: '#D08A00',
  wrong: '#96271D',
}

type Props = {
  summary: Summary
  accuracy: Accuracy | null
  ledger: LedgerRecord[]
  bank: BankRecord[]
  links: Link[]
  runId: string | null
  onReview: () => void
  onEvidence: () => void
}

export function SummaryScreen({
  summary,
  accuracy,
  ledger,
  bank,
  links,
  runId,
  onReview,
  onEvidence,
}: Props) {
  const [inspecting, setInspecting] = useState<AnyRecord | null>(null)
  const [cost, setCost] = useState<CostResponse | null>(null)

  // The cost split is only meaningful once the model has been asked - before
  // that the token and call figures are zero because nothing has happened yet,
  // which is a different zero from "served from cache" and would read the same.
  // So this waits for llm_complete rather than firing on mount.
  const llmDone = summary.llm_complete
  useEffect(() => {
    if (!runId || !llmDone) return
    let live = true
    api.cost(runId).then(
      (c) => {
        if (live) setCost(c)
      },
      () => {
        // A missing cost split is not worth an error state here - everything
        // above it is already on the page and still true without it.
      },
    )
    return () => {
      live = false
    }
  }, [runId, llmDone])

  // Derived, not cleared in an effect. A threshold change swaps this screen to
  // a *derived run* while the previous run's cost is still in state, and an
  // effect that clears it runs after the first paint - so for one frame the old
  // token count would sit under the new run's match rate. Matching on run_id
  // means the ticker is either this run's or absent.
  const shownCost = cost && cost.run_id === runId && llmDone ? cost : null

  return (
    <div className="mx-auto max-w-[1180px] px-4 py-6 sm:px-6">
      <Headline summary={summary} onReview={onReview} onEvidence={onEvidence} />

      {shownCost && <CostTicker cost={shownCost} />}

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_360px]">
        <Panel title="Where the engine resolved it">
          <PassBreakdown summary={summary} />
        </Panel>
        <Panel title="Exception queue">
          <QueueBreakdown summary={summary} onReview={onReview} />
        </Panel>
      </div>

      {accuracy && <AccuracyPanel accuracy={accuracy} summary={summary} />}

      <div
        className={`mt-4 grid gap-4 ${
          inspecting ? 'lg:grid-cols-[minmax(0,1fr)_360px]' : ''
        }`}
      >
        <Panel
          title="Ledger against statement"
          className="overflow-hidden"
          right={
            <span className="num text-[10px] text-mute">
              {links.length} links · click any row for its provenance
            </span>
          }
        >
          <MatchCanvas
            ledger={ledger}
            bank={bank}
            links={links}
            height={460}
            animateLines={false}
            selectedIds={inspecting ? new Set([inspecting.id]) : undefined}
            onSelect={setInspecting}
          />
          <div className="border-t border-rule">
            <CanvasLegend />
          </div>
        </Panel>

        <AnimatePresence>
          {inspecting && runId && (
            <ProvenancePanel
              key={inspecting.id}
              runId={runId}
              recordId={inspecting.id}
              onClose={() => setInspecting(null)}
            />
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ hero */
function Headline({
  summary,
  onReview,
  onEvidence,
}: {
  summary: Summary
  onReview: () => void
  onEvidence: () => void
}) {
  const total = summary.total_records
  const auto = summary.records_auto_resolved
  const proposed = summary.records_proposed
  const open = summary.records_unresolved

  return (
    <div className="sheet p-4 sm:p-5">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="label">Resolved by rule, no human, no model</div>
            <DatasetBadge profile={summary.dataset_profile} />
          </div>
          <div className="num mt-1 text-[clamp(44px,13vw,62px)] font-medium leading-[0.92] text-pine">
            <CountUp value={summary.match_rate_auto} format={(n) => pct(n, 2)} />
          </div>
          <p className="num mt-1 text-[11px] text-slate">
            {auto} of {total} records
          </p>
        </div>

        <div className="flex flex-wrap gap-x-8 gap-y-4">
          <Stat
            label="Incl. proposals awaiting you"
            size="lg"
            hint={`${proposed} records the engine has a candidate for, but will not commit`}
          >
            <span className="text-ochre">
              <CountUp value={summary.match_rate_with_proposed} format={(n) => pct(n, 2)} />
            </span>
          </Stat>
          <Stat
            label="Nothing found at all"
            size="lg"
            hint="genuinely unexplained, both sides"
          >
            <span className={open ? 'text-oxblood' : 'text-slate'}>
              <CountUp value={open} format={(n) => String(Math.round(n))} duration={700} />
            </span>
          </Stat>
        </div>
      </div>

      {/* the gap between the two numbers, drawn to scale */}
      <div className="mt-5">
        <div className="flex h-[26px] w-full overflow-hidden rounded-[2px] bg-bar">
          <Segment
            width={(auto / total) * 100}
            color={FILL.correct}
            label={`${auto} auto-resolved`}
            delay={0.1}
          />
          <Segment
            width={(proposed / total) * 100}
            color={FILL.missed}
            label={`${proposed} proposed`}
            delay={0.35}
          />
          <Segment
            width={(open / total) * 100}
            color={FILL.wrong}
            label={`${open} open`}
            delay={0.55}
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-[70ch] text-[12px] leading-relaxed text-slate">
            The middle band is the honest part. Those are records the engine found a
            plausible counterpart for and deliberately refused to commit — a summed
            batch, a damaged reference, a settlement that came up short. They are
            waiting for someone to look.
          </p>
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              onClick={onEvidence}
              className="inline-flex items-center gap-1.5 rounded-[3px] border border-rule bg-sheet px-3 py-2 text-[13px] font-medium transition-colors hover:border-ink"
            >
              <ScrollText size={13} /> See the evidence
            </button>
            <button
              type="button"
              onClick={onReview}
              className="rounded-[3px] bg-ink px-3.5 py-2 text-[13px] font-medium text-sheet transition-colors hover:bg-pine"
            >
              Review {summary.exceptions_total} exceptions
            </button>
          </div>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-rule pt-4 sm:flex sm:flex-wrap sm:gap-x-10">
        <Stat label="Records in play" size="sm">
          <CountUp value={total} format={(n) => String(Math.round(n))} />
        </Stat>
        <Stat label="Ledger / bank" size="sm">
          {summary.ledger_rows} / {summary.bank_rows}
        </Stat>
        <Stat label="Links committed" size="sm">
          {summary.links_auto} <span className="text-mute">+ {summary.links_proposed} proposed</span>
        </Stat>
        <Stat label="Duplicates flagged" size="sm">
          {summary.duplicates_flagged}
        </Stat>
        <Stat label="Value auto-resolved" size="sm" hint={`of INR ${compactMoney(summary.value_total)}`}>
          {pct(summary.value_rate_auto, 1)}
        </Stat>
        <Stat label="Engine time" size="sm" hint="all six passes">
          {summary.passes.reduce((a, p) => a + p.duration_ms, 0).toFixed(0)} ms
        </Stat>
      </div>
    </div>
  )
}

/**
 * Which set produced these numbers.
 *
 * Read off the run rather than off whatever the start screen had selected. A
 * threshold change adopts a *derived* run, and after that the toggle's state is
 * a record of what someone last clicked, not of what these figures came from.
 * The run knows; ask the run.
 */
function DatasetBadge({ profile }: { profile: string }) {
  if (profile === 'uploaded') return <Tag tone="slate">your files · no answer key</Tag>
  if (profile === 'stress') return <Tag tone="ochre">Adversarial set</Tag>
  if (profile === 'standard') return <Tag tone="pine">Standard set</Tag>
  return <Tag tone="slate">{humanise(profile)}</Tag>
}

/* ---------------------------------------------------------------- ticker */
/**
 * What the layering actually cost, counted up.
 *
 * The awkward figure here is zero, and it means three different things:
 *
 *   mock mode      no model was called at all, so the token count is not a
 *                  measurement of anything and must not be dressed as one
 *   fully cached   a real run whose verdicts all came from disk. Zero is the
 *                  true answer and it is the good news, not a missing value
 *   cold run       the only case where the measured token figure is real
 *
 * Animating a bare "0 tokens" would read identically in all three. So when the
 * backend says `tokens_measured: false` this shows the cold cost the same batch
 * *would* have carried - a figure the API already computes for exactly this
 * reason - and labels it an estimate rather than passing it off as measured.
 *
 * Hours saved is the one figure that is real in every mode: it comes off the
 * record counts and the queue size, and no model is involved in either.
 */
function CostTicker({ cost }: { cost: CostResponse }) {
  const { split, hours: saved } = cost
  const mock = split.mode === 'mock'
  const measured = split.tokens_measured
  const tokens = measured ? split.total_tokens : split.estimated_cold_tokens
  const cached = split.exceptions_served_from_cache

  const callsHint = mock
    ? 'the stand-in answered; no model was called'
    : split.api_calls === 0
      ? `all ${cached} verdicts came back from cache`
      : `${cached} of ${split.exceptions_requested} served from cache`

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
      className="sheet mt-4"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-3 py-2">
        <h2 className="label">What it cost</h2>
        <div className="flex flex-wrap items-center gap-1.5">
          {mock && <Tag tone="ochre">stand-in · no model called</Tag>}
          {!measured && <Tag tone="slate">tokens estimated</Tag>}
          {split.model && (
            <span className="num text-[10px] text-mute">{split.model}</span>
          )}
        </div>
      </header>

      {/* Three figures, ruled apart like columns on a statement. */}
      <div className="grid grid-cols-1 divide-y divide-rule sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <Tile
          label="Requests to the model"
          hint={callsHint}
          tone={split.api_calls === 0 ? 'text-pine' : 'text-ink'}
        >
          <CountUp
            value={split.api_calls}
            format={(n) => count(n)}
            duration={1000}
          />
        </Tile>

        <Tile
          label={measured ? 'Tokens used' : 'Tokens it would have cost'}
          hint={
            measured
              ? `${count(split.prompt_tokens)} prompt · ${count(split.completion_tokens)} completion`
              : 'estimated from the payloads that would have been sent'
          }
          tone="text-ink"
        >
          <CountUp value={tokens} format={(n) => count(n)} duration={1000} delay={90} />
        </Tile>

        <Tile
          label="Hours saved"
          hint={`${saved.manual_hours.toFixed(1)} h by hand, minus ${saved.hours_still_needed.toFixed(1)} h of queue`}
          tone="text-pine"
        >
          <CountUp
            value={saved.hours_saved}
            format={(n) => hours(n)}
            duration={1000}
            delay={180}
          />
        </Tile>
      </div>

      <p className="border-t border-rule px-3 py-2 text-[11px] leading-relaxed text-slate">
        {split.records_never_seen_by_model} of {split.total_records} records were
        resolved without a model seeing them. The queue is counted as real work
        rather than assumed free, which is why hours saved is lower than the match
        rate alone would suggest — both assumptions are exposed in{' '}
        <span className="num">GET /runs/{'{id}'}/cost</span>.
      </p>
    </motion.div>
  )
}

function Tile({
  label,
  hint,
  tone,
  children,
}: {
  label: string
  hint: string
  tone: string
  children: React.ReactNode
}) {
  return (
    <div className="min-w-0 px-3 py-3">
      <div className="label">{label}</div>
      <div className={`num mt-1 text-[clamp(24px,6vw,32px)] font-medium leading-none ${tone}`}>
        {children}
      </div>
      <div className="mt-1.5 text-[11px] leading-snug text-slate">{hint}</div>
    </div>
  )
}

function Segment({
  width,
  color,
  label,
  delay,
}: {
  width: number
  color: string
  label: string
  delay: number
}) {
  if (width <= 0) return null
  return (
    <motion.div
      className="relative flex items-center overflow-hidden"
      style={{ background: color, borderRight: '2px solid var(--color-sheet)' }}
      initial={{ width: 0 }}
      animate={{ width: `${width}%` }}
      transition={{ duration: 0.7, delay, ease: [0.22, 1, 0.36, 1] }}
      title={label}
    >
      {width > 9 && (
        <span className="num truncate px-2 text-[10px] font-medium text-white/95">{label}</span>
      )}
    </motion.div>
  )
}

/* -------------------------------------------------------- pass breakdown */
function PassBreakdown({ summary }: { summary: Summary }) {
  // Two of the passes resolve nothing by linking - they raise exceptions instead.
  // Plotting their link count would draw an empty bar next to real work.
  const rows = summary.passes
    .filter((p) => p.links_made > 0 || p.exceptions_raised > 0)
    .map((p) => ({
      ...p,
      count: p.records_resolved || p.exceptions_raised,
      raises: p.records_resolved === 0 && p.exceptions_raised > 0,
    }))
  const max = Math.max(...rows.map((p) => p.count), 1)

  return (
    <div className="greenbar">
      {rows.map((p) => (
        <div key={p.name} className="flex items-center gap-3 px-3 py-1.5">
          <span className="w-[104px] shrink-0 text-[12px] font-medium">{p.label}</span>
          <div className="h-[10px] flex-1 overflow-hidden rounded-[1px] bg-bar">
            <motion.div
              className="h-full"
              style={{
                background:
                  p.raises || p.name === 'composite' || p.name === 'fuzzy'
                    ? p.name === 'remainder'
                      ? FILL.wrong
                      : FILL.missed
                    : FILL.correct,
              }}
              initial={{ width: 0 }}
              animate={{ width: `${(p.count / max) * 100}%` }}
              transition={{ duration: 0.6, ease: 'easeOut' }}
            />
          </div>
          <span className="num w-[54px] shrink-0 text-right text-[11px]">
            {p.count}
            {p.raises && <span className="text-[9px] text-mute"> exc</span>}
          </span>
          <span className="num w-[52px] shrink-0 text-right text-[10px] text-mute">
            {p.duration_ms.toFixed(1)}ms
          </span>
        </div>
      ))}
      <p className="border-t border-rule px-3 py-2 text-[11px] leading-snug text-slate">
        Bars are records resolved, except the two marked <span className="num">exc</span>,
        which raise exceptions rather than links. Amber passes produce candidates only —
        those are the ones that reach the model, and then you.
      </p>
    </div>
  )
}

/* ------------------------------------------------------- queue breakdown */
function QueueBreakdown({ summary, onReview }: { summary: Summary; onReview: () => void }) {
  const entries = Object.entries(summary.exceptions_by_kind).sort((a, b) => b[1] - a[1])
  const total = summary.exceptions_total || 1

  return (
    <div>
      <div className="greenbar">
        {entries.map(([kind, n]) => (
          <button
            key={kind}
            type="button"
            onClick={onReview}
            className="flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors hover:bg-bar"
          >
            <span className="flex-1 text-[12px]">{humanise(kind)}</span>
            <div className="h-[8px] w-16 overflow-hidden rounded-[1px] bg-bar">
              <div
                className="h-full"
                style={{
                  width: `${(n / total) * 100}%`,
                  background:
                    kind.startsWith('unmatched') ? FILL.wrong : FILL.missed,
                }}
              />
            </div>
            <span className="num w-6 text-right text-[12px]">{n}</span>
          </button>
        ))}
      </div>
      <div className="border-t border-rule px-3 py-2">
        <p className="num text-[11px] text-slate">
          {summary.exceptions_needing_llm} of {summary.exceptions_total} were sent to the model
        </p>
        <p className="mt-0.5 text-[11px] leading-snug text-mute">
          Duplicates are not. A rule already explained those, so paying a model to
          re-explain them would be waste.
        </p>
      </div>
    </div>
  )
}

/* ----------------------------------------------------------- accuracy */
function AccuracyPanel({ accuracy, summary }: { accuracy: Accuracy; summary: Summary }) {
  // The category axis carries a label and a percentage. Below ~640px there is
  // not room for both at full width, so the labels truncate harder instead of
  // squeezing the plot area to nothing.
  const narrow = useIsNarrow(640)
  const data = accuracy.by_category.map((c) => ({
    name: humanise(c.category),
    correct: c.correct,
    missed: c.missed + c.duplicate_missed + c.escaped_review,
    wrong: c.wrong_link,
    cases: c.cases,
    accuracy: c.accuracy,
  }))

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: 0.2 }}
      className="mt-4"
    >
      <Panel
        title="Measured against the answer key"
        right={
          <span className="label flex items-center gap-1 text-ochre">
            <FlaskConical size={11} /> synthetic data only
          </span>
        }
      >
        <div className="border-b border-rule bg-ochre-soft/50 px-3 py-2">
          <p className="max-w-[100ch] text-[12px] leading-relaxed text-ink">
            This section exists only because we generated the data and held the answer key
            back from the engine. <strong className="font-semibold">Real reconciliation has
            no answer key.</strong> On your own files the honest measures are the two numbers
            at the top of this page — how much resolved by rule, and how big the queue is.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-5 px-4 py-4 lg:grid-cols-4 lg:gap-x-8">
          <Stat
            label="Cases fully correct"
            size="lg"
            hint={`${accuracy.cases_correct} of ${accuracy.cases_total}`}
          >
            <CountUp value={accuracy.case_accuracy} format={(n) => pct(n, 2)} />
          </Stat>
          <Stat
            label="Precision"
            size="lg"
            hint={`${accuracy.true_positives} right of ${accuracy.pairs_proposed_by_engine} proposed`}
          >
            <CountUp value={accuracy.precision} format={(n) => pct(n, 2)} />
          </Stat>
          <Stat
            label="Recall"
            size="lg"
            hint={`${accuracy.true_positives} found of ${accuracy.pairs_expected} real`}
          >
            <CountUp value={accuracy.recall} format={(n) => pct(n, 2)} />
          </Stat>
          <Stat
            label="Auto-resolve precision"
            size="lg"
            hint={`${accuracy.auto_false_positives} wrong among ${accuracy.auto_pairs} the engine committed alone`}
          >
            <span className="flex items-center gap-1.5">
              <ShieldCheck size={20} className="text-pine" />
              <CountUp value={accuracy.auto_precision} format={(n) => pct(n, 2)} />
            </span>
          </Stat>
        </div>

        <div className="border-t border-rule px-2 pb-1 pt-3">
          <div className="mb-1 flex flex-wrap items-center gap-4 px-2">
            <span className="label">Case outcomes by injected flaw</span>
            <Legend />
          </div>
          <ResponsiveContainer width="100%" height={Math.max(220, data.length * 26 + 34)}>
            <BarChart
              data={data}
              layout="vertical"
              margin={{ top: 2, right: 18, left: 4, bottom: 2 }}
              barCategoryGap={5}
            >
              <CartesianGrid
                horizontal={false}
                stroke="var(--color-rule)"
                strokeOpacity={0.55}
              />
              <XAxis
                type="number"
                tick={{ fontSize: 10, fill: 'var(--color-mute)', fontFamily: 'var(--font-mono)' }}
                axisLine={{ stroke: 'var(--color-rule)' }}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={narrow ? 132 : 196}
                axisLine={false}
                tickLine={false}
                interval={0}
                tick={(props) => <CategoryTick {...props} rows={data} narrow={narrow} />}
              />
              <Tooltip
                cursor={{ fill: 'var(--color-bar)', fillOpacity: 0.55 }}
                content={<CategoryTooltip />}
              />
              <Bar dataKey="correct" stackId="a" fill={FILL.correct} name="Correct" />
              <Bar dataKey="missed" stackId="a" fill={FILL.missed} name="Missed" />
              <Bar
                dataKey="wrong"
                stackId="a"
                fill={FILL.wrong}
                name="Wrong link"
                radius={[0, 3, 3, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <details className="border-t border-rule">
          <summary className="label cursor-pointer px-3 py-2 hover:text-ink">
            The same numbers as a table
          </summary>
          <div className="scroll-x">
          <table className="w-full min-w-[620px]">
            <thead>
              <tr className="border-y border-rule">
                {['Injected flaw', 'Cases', 'Correct', 'Missed', 'Wrong', 'Auto', 'Proposed', 'Accuracy'].map(
                  (h, i) => (
                    <th
                      key={h}
                      className={`label px-3 py-1.5 ${i === 0 ? 'text-left' : 'text-right'}`}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="greenbar">
              {accuracy.by_category.map((c) => (
                <tr key={c.category}>
                  <td className="px-3 py-1 text-[12px]">{humanise(c.category)}</td>
                  <td className="num px-3 py-1 text-right text-[12px]">{c.cases}</td>
                  <td className="num px-3 py-1 text-right text-[12px] text-pine">{c.correct}</td>
                  <td className="num px-3 py-1 text-right text-[12px]">
                    {c.missed + c.duplicate_missed + c.escaped_review || '—'}
                  </td>
                  <td className="num px-3 py-1 text-right text-[12px]">{c.wrong_link || '—'}</td>
                  <td className="num px-3 py-1 text-right text-[12px] text-mute">{c.auto}</td>
                  <td className="num px-3 py-1 text-right text-[12px] text-mute">{c.proposed}</td>
                  <td className="num px-3 py-1 text-right text-[12px] font-medium">
                    {pct(c.accuracy, 1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </details>

        <p className="border-t border-rule px-3 py-2 text-[11px] leading-relaxed text-slate">
          Dataset: <span className="num">{summary.dataset_profile}</span>. Precision is what
          matters most here — a wrong match is worse than a match you had to make yourself,
          because a wrong one is silent.
        </p>
      </Panel>
    </motion.div>
  )
}

/* Recharts hands a custom tick every axis prop it holds, typed loosely - x and
   y arrive as `string | number`. Narrowing them here rather than at the call
   site keeps the JSX readable and the component honest about what it receives. */
function CategoryTick({
  x,
  y,
  payload,
  rows,
  narrow,
}: {
  x?: string | number
  y?: string | number
  payload?: { value?: string | number }
  rows: { name: string; accuracy: number }[]
  narrow?: boolean
}) {
  const name = String(payload?.value ?? '')
  const row = rows.find((r) => r.name === name)
  const limit = narrow ? 14 : 24
  return (
    <g transform={`translate(${Number(x) || 0},${Number(y) || 0})`}>
      <text
        x={narrow ? -40 : -52}
        dy={4}
        textAnchor="end"
        fontSize={narrow ? 10 : 11}
        fill="var(--color-ink)"
      >
        {name.length > limit ? name.slice(0, limit - 1) + '…' : name}
      </text>
      {row && (
        <text
          x={-8}
          dy={4}
          textAnchor="end"
          fontSize={10}
          fontFamily="var(--font-mono)"
          fill={row.accuracy === 1 ? 'var(--color-pine)' : 'var(--color-oxblood)'}
        >
          {pct(row.accuracy, 0)}
        </text>
      )}
    </g>
  )
}

function Legend() {
  const items: [string, string][] = [
    [FILL.correct, 'Correct'],
    [FILL.missed, 'Missed'],
    [FILL.wrong, 'Wrong link'],
  ]
  return (
    <div className="flex gap-3.5">
      {items.map(([color, label]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[2px]" style={{ background: color }} />
          <span className="text-[11px] text-slate">{label}</span>
        </span>
      ))}
    </div>
  )
}

function CategoryTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: { payload: { name: string; cases: number; correct: number; missed: number; wrong: number; accuracy: number } }[]
}) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="sheet px-2.5 py-2 shadow-sm">
      <div className="text-[12px] font-semibold">{d.name}</div>
      <div className="num mt-1 space-y-0.5 text-[11px]">
        <Line label="cases" value={d.cases} />
        <Line label="correct" value={d.correct} color="text-pine" />
        {d.missed > 0 && <Line label="missed" value={d.missed} color="text-ochre" />}
        {d.wrong > 0 && <Line label="wrong link" value={d.wrong} color="text-oxblood" />}
        <Line label="accuracy" value={pct(d.accuracy, 1)} />
      </div>
    </div>
  )
}

function Line({
  label,
  value,
  color = 'text-ink',
}: {
  label: string
  value: number | string
  color?: string
}) {
  return (
    <div className="flex justify-between gap-5">
      <span className="text-mute">{label}</span>
      <span className={color}>{value}</span>
    </div>
  )
}

export { money }
