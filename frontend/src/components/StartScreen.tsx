import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, FileUp, Play, X } from 'lucide-react'
import type { DatasetInfo, Health } from '../lib/api'
import { api } from '../lib/api'
import { humanise } from '../lib/format'
import { Panel, Pulse } from './bits'

type Props = {
  onStart: (mode: { kind: 'bundled'; dataset: string } | { kind: 'upload'; ledger: File; bank: File }) => void
  error: string | null
}

export function StartScreen({ onStart, error }: Props) {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [chosen, setChosen] = useState('standard')
  const [ledgerFile, setLedgerFile] = useState<File | null>(null)
  const [bankFile, setBankFile] = useState<File | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api
      .datasets()
      .then((d) => setDatasets(d.datasets))
      .catch((e) => setLoadError(e.message))
    api.health().then(setHealth).catch(() => undefined)
  }, [])

  const canUpload = ledgerFile && bankFile

  return (
    <div className="mx-auto max-w-[1080px] px-4 py-8 sm:px-6 sm:py-10">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <h1 className="text-[clamp(21px,5.5vw,27px)] font-semibold leading-tight">
          Two records of the same money, and they never agree.
        </h1>
        <p className="mt-2 max-w-[68ch] text-[14px] leading-relaxed text-slate sm:text-[15px]">
          Six deterministic passes resolve everything they can prove. Whatever survives
          them — and only that — goes to a language model for a plain-English opinion.
          Nothing is written off, refunded or cleared without you clicking it.
        </p>
      </motion.div>

      {(error || loadError) && (
        <div className="mt-6 flex items-start gap-2 rounded-[3px] border border-oxblood/35 bg-oxblood-soft px-3 py-2.5">
          <AlertTriangle size={15} className="mt-[2px] shrink-0 text-oxblood" />
          <p className="text-[13px] leading-snug text-oxblood">{error || loadError}</p>
        </div>
      )}

      <div className="mt-8">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="label">Run on bundled data</div>
          <DatasetToggle value={chosen} onChange={setChosen} disabled={!datasets} />
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {!datasets && (
            <>
              <Pulse className="h-[168px]" />
              <Pulse className="h-[168px]" />
            </>
          )}
          {datasets?.map((d) => (
            <DatasetCard
              key={d.profile}
              info={d}
              selected={chosen === d.profile}
              onSelect={() => setChosen(d.profile)}
            />
          ))}
        </div>

        <button
          type="button"
          disabled={!datasets}
          onClick={() => onStart({ kind: 'bundled', dataset: chosen })}
          className="mt-4 inline-flex items-center gap-2 rounded-[3px] bg-ink px-4 py-2.5 text-[13px] font-medium text-sheet transition-colors hover:bg-pine disabled:opacity-40"
        >
          <Play size={14} />
          Reconcile the {chosen === 'stress' ? 'adversarial' : 'standard'} set
        </button>
      </div>

      <div className="mt-10">
        <div className="label mb-2">Or use your own two files</div>
        <Panel>
          <div className="grid gap-3 p-3 md:grid-cols-2">
            <FilePicker
              label="Ledger CSV"
              hint="txn_id, date, amount, counterparty, payment_method, reference_number, status"
              file={ledgerFile}
              onPick={setLedgerFile}
            />
            <FilePicker
              label="Bank statement CSV"
              hint="stmt_id, date, amount, reference_number, narration, type"
              file={bankFile}
              onPick={setBankFile}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3 border-t border-rule px-3 py-2.5">
            <button
              type="button"
              disabled={!canUpload}
              onClick={() =>
                canUpload && onStart({ kind: 'upload', ledger: ledgerFile!, bank: bankFile! })
              }
              className="inline-flex items-center gap-2 rounded-[3px] border border-rule bg-sheet px-3 py-1.5 text-[13px] font-medium transition-colors hover:border-ink disabled:opacity-40"
            >
              <FileUp size={14} />
              Reconcile these
            </button>
            <p className="text-[11px] leading-snug text-slate">
              Uploaded files get no accuracy score. There is no answer key for real data —
              that is the honest state of production reconciliation, and pretending
              otherwise would undercut the point.
            </p>
          </div>
        </Panel>
      </div>

      <GroqNotice health={health} />
    </div>
  )
}

/**
 * Standard / Adversarial, as a segmented control.
 *
 * It drives the same `chosen` state the cards below it do rather than holding
 * its own - two controls for one value that each remember it separately is how
 * you end up running the set the user did not pick. The cards stay because they
 * carry the flaw mix; this is for someone who already knows which one they want
 * and does not want to read two paragraphs to switch.
 */
function DatasetToggle({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const options: [string, string][] = [
    ['standard', 'Standard'],
    ['stress', 'Adversarial'],
  ]
  return (
    <div
      role="radiogroup"
      aria-label="Bundled dataset"
      className="inline-flex overflow-hidden rounded-[3px] border border-rule bg-sheet"
    >
      {options.map(([key, label], i) => {
        const on = value === key
        return (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={on}
            disabled={disabled}
            onClick={() => onChange(key)}
            className={`px-3 py-1.5 text-[12px] font-medium transition-colors disabled:opacity-40 ${
              i > 0 ? 'border-l border-rule' : ''
            } ${on ? 'bg-ink text-sheet' : 'text-slate hover:text-ink'}`}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}

function DatasetCard({
  info,
  selected,
  onSelect,
}: {
  info: DatasetInfo
  selected: boolean
  onSelect: () => void
}) {
  const isStress = info.profile === 'stress'
  const top = Object.entries(info.categories)
    .sort((a, b) => b[1] - a[1])
    .slice(0, isStress ? 6 : 5)

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`sheet block p-3 text-left transition-all ${
        selected ? 'border-ink shadow-[0_0_0_1px_var(--color-ink)]' : 'hover:border-mute'
      }`}
    >
      <div className="flex items-baseline justify-between">
        <h3 className="text-[15px] font-semibold">
          {isStress ? 'Adversarial' : 'Standard'}
        </h3>
        <span className="num text-[10px] text-mute">seed {info.seed}</span>
      </div>
      <p className="mt-1 text-[12px] leading-snug text-slate">
        {isStress
          ? 'Every category below, plus four the thresholds were never designed around: ambiguous truncated references, short settlements, blank reference columns, T+12 delays.'
          : 'The standard flaw mix — timing gaps, gateway fees, double webhook fires, batched settlements, typos, refunds, and genuine orphans.'}
      </p>
      <div className="mt-2.5 flex gap-4 border-t border-rule pt-2">
        <Figure label="cases" value={info.cases} />
        <Figure label="ledger" value={info.ledger_rows} />
        <Figure label="bank" value={info.bank_rows} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {top.map(([cat, n]) => (
          <span
            key={cat}
            className="num rounded-[2px] bg-bar px-1.5 py-[1px] text-[10px] text-slate"
          >
            {humanise(cat)} {n}
          </span>
        ))}
      </div>
    </button>
  )
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="num text-[17px] font-medium">{value}</div>
      <div className="label">{label}</div>
    </div>
  )
}

function FilePicker({
  label,
  hint,
  file,
  onPick,
}: {
  label: string
  hint: string
  file: File | null
  onPick: (f: File | null) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const f = e.dataTransfer.files?.[0]
        if (f) onPick(f)
      }}
      className={`rounded-[3px] border border-dashed p-3 transition-colors ${
        over ? 'border-pine bg-pine-soft' : file ? 'border-pine/40 bg-sheet' : 'border-rule'
      }`}
    >
      <div className="label">{label}</div>
      {file ? (
        <div className="mt-1.5 flex items-center gap-2">
          <span className="num truncate text-[12px]">{file.name}</span>
          <button
            type="button"
            onClick={() => onPick(null)}
            className="text-mute transition-colors hover:text-oxblood"
            aria-label={`Remove ${file.name}`}
          >
            <X size={13} />
          </button>
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={() => ref.current?.click()}
            className="mt-1.5 text-[12px] underline decoration-rule underline-offset-2 hover:decoration-ink"
          >
            Choose a file, or drop one here
          </button>
          <p className="num mt-1.5 text-[10px] leading-snug text-mute">{hint}</p>
        </>
      )}
      <input
        ref={ref}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(e) => onPick(e.target.files?.[0] ?? null)}
      />
    </div>
  )
}

function GroqNotice({ health }: { health: Health | null }) {
  if (!health || health.groq_reachable) return null
  return (
    <div className="mt-8 rounded-[3px] border border-ochre/35 bg-ochre-soft px-3 py-2.5">
      <div className="label text-ochre">Language model not connected</div>
      <p className="mt-1 max-w-[76ch] text-[12px] leading-relaxed text-ink">
        The deterministic engine runs regardless — it is the part doing the heavy lifting,
        and it needs no key. Exceptions that would have gone to the model will be marked{' '}
        <span className="num">unavailable</span> rather than filled with a generated
        explanation. Add <span className="num">GROQ_API_KEY</span> to{' '}
        <span className="num">backend/.env</span> and run{' '}
        <span className="num">python scripts/check_groq.py</span> to switch it on.
      </p>
    </div>
  )
}
