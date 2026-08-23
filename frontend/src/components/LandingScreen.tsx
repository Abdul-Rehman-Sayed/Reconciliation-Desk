import { motion } from 'framer-motion'
import { ArrowRight, CircleSlash, Cpu, ShieldCheck, Sparkles } from 'lucide-react'

/**
 * The page before the desk.
 *
 * Every figure on it is one the repo can reproduce - `scripts/evaluate.py` for
 * the match rates, `scripts/baseline.py` for the naive join, `pytest` for the
 * test count - and each one says which dataset it came from. A landing page for
 * a reconciliation tool is the worst possible place to round a number up, since
 * the entire pitch underneath it is that this thing does not overstate what it
 * knows.
 */
export function LandingScreen({ onEnter }: { onEnter: () => void }) {
  return (
    <div className="min-h-screen bg-field">
      <Hero onEnter={onEnter} />
      <Layers />
      <Results />
      <Limits onEnter={onEnter} />
    </div>
  )
}

/* ------------------------------------------------------------------ hero */
function Hero({ onEnter }: { onEnter: () => void }) {
  return (
    <header className="border-b border-rule bg-sheet">
      <div className="mx-auto max-w-[1080px] px-4 py-14 sm:px-6 sm:py-20">
        <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1fr)_360px]">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45 }}
        >
          <div className="flex items-center gap-2.5">
            <Mark />
            <div>
              <div className="text-[14px] font-semibold leading-tight">
                Reconciliation Desk
              </div>
              <div className="label" style={{ letterSpacing: '0.13em' }}>
                AI Finance Controller
              </div>
            </div>
          </div>

          <h1 className="mt-8 max-w-[19ch] text-[clamp(30px,7.5vw,54px)] font-semibold leading-[1.04] tracking-[-0.02em]">
            Two records of the same money.
            <span className="block text-pine">They never agree.</span>
          </h1>

          <p className="mt-5 max-w-[64ch] text-[15px] leading-relaxed text-slate sm:text-[16px]">
            Settlements land three days late. The gateway takes its fee before the money
            arrives. A webhook fires twice. Four payments settle as one credit that
            matches none of them. Today a person works through all of it by hand, every
            month.
          </p>
          <p className="mt-3 max-w-[64ch] text-[15px] leading-relaxed sm:text-[16px]">
            This automates the part that is mechanical, explains the part that is not,
            and <span className="font-medium text-ink">refuses to touch the money
            either way.</span>
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={onEnter}
              className="inline-flex items-center gap-2 rounded-[3px] bg-ink px-5 py-2.5 text-[14px] font-medium text-sheet transition-colors hover:bg-pine"
            >
              Open the desk <ArrowRight size={15} />
            </button>
            <span className="num text-[11px] text-mute">
              runs on bundled data · no key required
            </span>
          </div>
        </motion.div>

          {/* The two-column weave is the product's signature view, so the hero
              shows the thing itself rather than an illustration of it: ledger
              left, statement right, one line per committed pair, and the rows
              with no line attached are the exceptions. */}
          <HeroWeave />
        </div>

        {/* The headline figures, ruled like a statement footer. */}
        <motion.dl
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5, delay: 0.15 }}
          className="mt-12 grid grid-cols-2 gap-x-6 gap-y-5 border-t border-rule pt-6 sm:grid-cols-4"
        >
          <HeroFigure value="90.19%" label="resolved by rule" note="no human, no model" />
          <HeroFigure value="< 4%" label="reaches the model" note="32 of 856 records" />
          <HeroFigure value="100%" label="auto-resolve precision" note="0 wrong commits" />
          <HeroFigure value="23 ms" label="deterministic engine" note="all six passes" />
        </motion.dl>
        <p className="num mt-4 text-[10px] leading-relaxed text-mute">
          Measured on the bundled standard set (856 records) — reproduce with
          <span className="text-slate"> python scripts/evaluate.py</span>
        </p>
      </div>
    </header>
  )
}

/**
 * The hero's ledger weave.
 *
 * Not decoration: it is a scaled-down version of the canvas the app draws after
 * a real run. Ledger rows down the left, statement rows down the right, one
 * curve per pair the engine committed, coloured by whether it resolved the pair
 * outright or only proposed it. Two rows are deliberately left with no line
 * attached, because that is what an exception looks like on the real screen.
 *
 * Static geometry, hand-set rather than generated, so it reads as a plausible
 * healthy run - a near-diagonal weave with a few crossings - instead of noise.
 */
const WEAVE_ROWS = 16
const rowY = (i: number) => 16 + i * 21

/** [ledger row, statement row, committed?] - rows 11 and 14 are left out. */
const WEAVE_PAIRS: [number, number, boolean][] = [
  [0, 0, true],
  [1, 2, true],
  [2, 1, true],
  [3, 3, true],
  [4, 5, true],
  [5, 4, true],
  [6, 6, true],
  [7, 8, false],
  [8, 7, true],
  [9, 9, true],
  [10, 10, true],
  [12, 12, false],
  [13, 11, true],
  [15, 13, true],
]

function HeroWeave() {
  const W = 360
  const H = rowY(WEAVE_ROWS - 1) + 16
  const leftEnd = 96
  const rightStart = W - 96
  const linked = new Set(WEAVE_PAIRS.map(([l]) => l))
  const linkedRight = new Set(WEAVE_PAIRS.map(([, r]) => r))

  return (
    <motion.svg
      viewBox={`0 0 ${W} ${H}`}
      className="hidden h-auto w-full lg:block"
      role="img"
      aria-label="Ledger rows on the left joined to bank statement rows on the right, with two rows left unmatched"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6, delay: 0.2 }}
    >
      <text x="4" y="8" className="num" fontSize="7" fill="var(--color-mute)">
        LEDGER
      </text>
      <text x={W - 4} y="8" textAnchor="end" className="num" fontSize="7" fill="var(--color-mute)">
        STATEMENT
      </text>

      {Array.from({ length: WEAVE_ROWS }, (_, i) => {
        const lOn = linked.has(i)
        const rOn = linkedRight.has(i)
        return (
          <g key={i}>
            <rect
              x="4"
              y={rowY(i) - 4}
              width={leftEnd - 4}
              height="8"
              rx="1"
              fill={lOn ? 'var(--color-bar)' : 'var(--color-oxblood-soft)'}
            />
            <rect
              x={rightStart}
              y={rowY(i) - 4}
              width={W - 4 - rightStart}
              height="8"
              rx="1"
              fill={rOn ? 'var(--color-bar)' : 'var(--color-oxblood-soft)'}
            />
          </g>
        )
      })}

      {WEAVE_PAIRS.map(([l, r, committed], i) => {
        const y1 = rowY(l)
        const y2 = rowY(r)
        const d = `M ${leftEnd} ${y1} C ${leftEnd + 58} ${y1}, ${rightStart - 58} ${y2}, ${rightStart} ${y2}`
        return (
          <motion.path
            key={`${l}-${r}`}
            d={d}
            fill="none"
            stroke={committed ? 'var(--color-pine)' : 'var(--color-ochre)'}
            strokeWidth="1.25"
            strokeOpacity={committed ? 0.75 : 0.9}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.7, delay: 0.35 + i * 0.045, ease: 'easeOut' }}
          />
        )
      })}
    </motion.svg>
  )
}

function HeroFigure({
  value,
  label,
  note,
}: {
  value: string
  label: string
  note: string
}) {
  return (
    <div className="min-w-0">
      <dt className="label">{label}</dt>
      <dd className="num mt-1 text-[clamp(22px,5.5vw,30px)] font-medium leading-none text-pine">
        {value}
      </dd>
      <dd className="mt-1 text-[11px] leading-snug text-slate">{note}</dd>
    </div>
  )
}

/* ---------------------------------------------------------------- layers */
const LAYERS = [
  {
    icon: Cpu,
    n: '01',
    title: 'A deterministic engine does the heavy lifting',
    body: 'Six passes in plain Python, no AI in the file. Exact matching, then gateway-fee formulas and T+3 delays, reversal pairing, duplicate detection, batched settlements, fuzzy references. Every threshold is a named constant, not a magic number buried in a conditional.',
    figure: '90.19% resolved here',
  },
  {
    icon: Sparkles,
    n: '02',
    title: 'The model only ever sees what survived',
    body: 'It is asked about 32 of 856 records and never sees a clean match. Its job is held to a JSON schema: classify, explain in language an ops person can read, propose an action, state its own confidence. It classifies and explains. It does not resolve.',
    figure: '3 requests for a cold run',
  },
  {
    icon: ShieldCheck,
    n: '03',
    title: 'Nothing gets past a human',
    body: 'Every exception lands in a queue with approve, reject, investigate. There is no auto-execute path anywhere in the codebase — the action route records what a person decided and nothing else. The agent’s opinion is an opinion sitting in a queue.',
    figure: '0 auto-executed actions',
  },
]

function Layers() {
  return (
    <section className="mx-auto max-w-[1080px] px-4 py-14 sm:px-6 sm:py-16">
      <h2 className="text-[clamp(20px,4.5vw,26px)] font-semibold leading-tight">
        Three layers, and the order is the whole argument
      </h2>
      <p className="mt-2 max-w-[68ch] text-[14px] leading-relaxed text-slate">
        The split is visible in the API on purpose. Reconciling returns in about 100 ms
        without touching a model; explaining is a separate call you have to make. The
        interface runs them in that order, so what you watch on screen is the order the
        work actually happened in.
      </p>

      <div className="mt-7 grid gap-3 md:grid-cols-3">
        {LAYERS.map((l, i) => (
          <motion.article
            key={l.n}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: i * 0.08 }}
            className="sheet flex flex-col p-4"
          >
            <div className="flex items-center justify-between">
              <l.icon size={17} className="text-pine" />
              <span className="num text-[11px] text-mute">{l.n}</span>
            </div>
            <h3 className="mt-3 text-[15px] font-semibold leading-snug">{l.title}</h3>
            <p className="mt-2 flex-1 text-[13px] leading-relaxed text-slate">{l.body}</p>
            <div className="num mt-3 border-t border-rule pt-2 text-[11px] text-pine">
              {l.figure}
            </div>
          </motion.article>
        ))}
      </div>
    </section>
  )
}

/* --------------------------------------------------------------- results */
const ROWS: [string, string, string][] = [
  ['Records reconciled', '856', '877'],
  ['Auto-resolved by rule', '90.19%', '83.01%'],
  ['Including model proposals', '99.07%', '99.09%'],
  ['Left genuinely unresolved', '8', '8'],
  ['Case accuracy vs ground truth', '100.00%', '100.00%'],
  ['Auto-resolve precision', '100.00%', '100.00%'],
]

function Results() {
  return (
    <section className="border-y border-rule bg-sheet">
      <div className="mx-auto max-w-[1080px] px-4 py-14 sm:px-6 sm:py-16">
        <h2 className="text-[clamp(20px,4.5vw,26px)] font-semibold leading-tight">
          Harder data does not make it wrong. It makes it ask for more help.
        </h2>
        <p className="mt-2 max-w-[68ch] text-[14px] leading-relaxed text-slate">
          The adversarial set keeps every category the brief specifies and adds four the
          thresholds were never designed around. The gap between 83% and 99% there is 141
          records the engine found a candidate for and deliberately refused to commit —
          which is the behaviour you want from anything standing between you and a
          payments ledger.
        </p>

        <div className="scroll-x mt-6">
          <table className="w-full min-w-[420px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-rule">
                <th className="label py-2 text-left">Measure</th>
                <th className="label py-2 text-right">Standard</th>
                <th className="label py-2 text-right">Adversarial</th>
              </tr>
            </thead>
            <tbody className="greenbar">
              {ROWS.map(([label, a, b]) => (
                <tr key={label}>
                  <td className="py-2 pr-4">{label}</td>
                  <td className="num py-2 text-right">{a}</td>
                  <td className="num py-2 text-right">{b}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="num mt-3 text-[10px] leading-relaxed text-mute">
          78 tests, none of which make a network call · a naive reference-and-amount join
          scores 76.64% recall on the same data
        </p>
      </div>
    </section>
  )
}

/* ---------------------------------------------------------------- limits */
const LIMITS = [
  'Uploaded files get no accuracy score. There is no answer key for real data, and the API returns 404 rather than a fabricated number.',
  'Ground truth exists only for the two bundled synthetic datasets. We generated the data and wrote the matcher, so a 100% off one dataset would be worth very little — the thresholds are held out against nine unseen seeds per profile for that reason.',
  'A batch of five or more ledger rows is not searched. The date windows are calibrated for Indian PG settlement and would need re-tuning for another rail.',
]

function Limits({ onEnter }: { onEnter: () => void }) {
  return (
    <section className="mx-auto max-w-[1080px] px-4 py-14 sm:px-6 sm:py-16">
      <div className="flex items-start gap-2.5">
        <CircleSlash size={17} className="mt-[3px] shrink-0 text-ochre" />
        <div>
          <h2 className="text-[clamp(19px,4.2vw,24px)] font-semibold leading-tight">
            What it does not claim
          </h2>
          <p className="mt-2 max-w-[68ch] text-[14px] leading-relaxed text-slate">
            Real bank exports are messier than anything we thought to generate, and the
            failure modes we did not imagine are exactly the ones not in here.
          </p>
        </div>
      </div>

      <ul className="mt-5 space-y-2">
        {LIMITS.map((l) => (
          <li
            key={l}
            className="sheet px-3 py-2.5 text-[13px] leading-relaxed text-slate"
          >
            {l}
          </li>
        ))}
      </ul>

      <div className="mt-10 flex flex-wrap items-center justify-between gap-4 border-t border-rule pt-8">
        <div>
          <div className="text-[15px] font-semibold">Run it on the bundled data.</div>
          <p className="mt-1 text-[13px] text-slate">
            Standard or adversarial, both scored against ground truth.
          </p>
        </div>
        <button
          type="button"
          onClick={onEnter}
          className="inline-flex items-center gap-2 rounded-[3px] bg-ink px-5 py-2.5 text-[14px] font-medium text-sheet transition-colors hover:bg-pine"
        >
          Open the desk <ArrowRight size={15} />
        </button>
      </div>
    </section>
  )
}

/** Two columns, one rule between them. The whole problem in nine strokes. */
function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
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
