import { X } from 'lucide-react'
import type { BankRecord, ExceptionRow, LedgerRecord } from '../lib/api'
import { hasVerdict, humanise, money, shortDate } from '../lib/format'
import { SidePanel, Tag } from './bits'


export function ExceptionDetail({
  exception,
  onClose,
  onAct,
  busy,
}: {
  exception: ExceptionRow
  onClose: () => void
  onAct: (action: string) => void
  busy: boolean
}) {
  const ledger = exception.ledger_records ?? []
  const bank = exception.bank_records ?? []
  const ev = exception.evidence ?? {}

  const ledgerTotal = ledger.reduce((a, r) => a + r.amount, 0)
  const bankTotal = bank.reduce((a, r) => a + r.amount, 0)
  const gap = Math.abs(ledgerTotal) - Math.abs(bankTotal)

  return (
    <SidePanel onClose={onClose} label={`Exception ${exception.exception_id}`}>
      <header className="sticky top-0 z-10 flex items-start justify-between gap-2 border-b border-rule bg-sheet px-3 py-2.5">
        <div>
          <div className="num text-[10px] text-mute">{exception.exception_id}</div>
          <h3 className="text-[14px] font-semibold">{humanise(exception.kind)}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="-m-1.5 p-1.5 text-mute transition-colors hover:text-ink"
          aria-label="Close detail"
        >
          <X size={16} />
        </button>
      </header>

      <div className="px-3 py-2.5">
        <div className="label mb-1">What the engine found</div>
        <p className="text-[12px] leading-relaxed">{exception.engine_note}</p>
      </div>

      {exception.llm && (
        <div className="border-t border-rule px-3 py-2.5">
          <div className="label mb-1 flex flex-wrap items-center gap-2">
            <span>
              {exception.llm.source === 'mock' ? 'What the stand-in says' : 'What the model thinks'}
            </span>
            {hasVerdict(exception.llm.source) ? (
              <>
                {exception.llm.source === 'mock' && <Tag tone="ochre">stand-in</Tag>}
                {exception.llm.cached && <Tag tone="slate">cached</Tag>}
              </>
            ) : (
              <Tag tone="ochre">unavailable</Tag>
            )}
          </div>
          <p className="text-[12px] leading-relaxed">{exception.llm.explanation}</p>
        </div>
      )}

      <div className="border-t border-rule">
        <div className="grid grid-cols-1 divide-y divide-rule sm:grid-cols-2 sm:divide-x sm:divide-y-0">
          <Side title={`Ledger · ${ledger.length}`} empty="Nothing on this side">
            {ledger.map((r) => (
              <LedgerCard key={r.id} r={r} />
            ))}
          </Side>
          <Side title={`Statement · ${bank.length}`} empty="Nothing on this side">
            {bank.map((r) => (
              <BankCard key={r.id} r={r} />
            ))}
          </Side>
        </div>

        {ledger.length > 0 && bank.length > 0 && (
          <div className="border-t border-rule px-3 py-2">
            <div className="label mb-1">The difference</div>
            <dl className="num space-y-1 text-[12px]">
              <Row label="Ledger total" value={money(ledgerTotal)} />
              <Row label="Statement total" value={money(bankTotal)} />
              <Row
                label="Gap"
                value={money(gap)}
                tone={Math.abs(gap) < 0.01 ? 'text-pine' : 'text-oxblood'}
              />
              {typeof ev.fee_rate === 'number' && (
                <Row
                  label="Implied fee rate"
                  value={`${(ev.fee_rate * 100).toFixed(2)}%${ev.known_rate ? ' (known)' : ' (unrecognised)'}`}
                  tone="text-ochre"
                />
              )}
              {typeof ev.shortfall_pct === 'number' && (
                <Row
                  label="Short by"
                  value={`${ev.shortfall_pct.toFixed(2)}%`}
                  tone="text-oxblood"
                />
              )}
              {typeof ev.day_delta === 'number' && (
                <Row label="Days apart" value={String(ev.day_delta)} />
              )}
              {typeof ev.ref_similarity === 'number' && (
                <Row label="Reference match" value={`${ev.ref_similarity.toFixed(0)}%`} />
              )}
            </dl>
          </div>
        )}
      </div>

      <EvidenceBlock evidence={ev} />

      <footer className="sticky bottom-0 border-t border-rule bg-sheet px-3 py-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))]">
        <div className="label mb-1.5">Your call</div>
        <div className="grid grid-cols-3 gap-1.5">
          <ActBtn label="Approve" id={exception.exception_id} tone="pine"
                  onClick={() => onAct('approve')} busy={busy} />
          <ActBtn label="Reject" id={exception.exception_id} tone="oxblood"
                  onClick={() => onAct('reject')} busy={busy} />
          <ActBtn label="Investigate" id={exception.exception_id} tone="ochre"
                  onClick={() => onAct('investigate')} busy={busy} />
        </div>
      </footer>
    </SidePanel>
  )
}

function Side({
  title,
  children,
  empty,
}: {
  title: string
  children: React.ReactNode
  empty: string
}) {
  const has = Array.isArray(children) ? children.length > 0 : Boolean(children)
  return (
    <div className="min-w-0">
      <div className="label border-b border-rule px-2.5 py-1.5">{title}</div>
      <div className="space-y-2 p-2.5">
        {has ? children : <p className="text-[11px] italic text-oxblood">{empty}</p>}
      </div>
    </div>
  )
}

function LedgerCard({ r }: { r: LedgerRecord }) {
  return (
    <div className="rounded-[2px] border border-rule bg-field/50 p-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="num text-[10px] text-mute">{r.id}</span>
        <span className="num text-[13px] font-medium">{money(r.amount)}</span>
      </div>
      <div className="num mt-1 truncate text-[10px]" title={r.reference_number}>
        {r.reference_number || '—'}
      </div>
      <div className="mt-1 truncate text-[11px] text-slate" title={r.counterparty}>
        {r.counterparty}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="num text-[10px] text-mute">{shortDate(r.date)}</span>
        <span className="num text-[10px] text-mute">{r.payment_method}</span>
        <Tag tone={r.status === 'captured' ? 'slate' : 'ochre'}>{r.status}</Tag>
      </div>
    </div>
  )
}

function BankCard({ r }: { r: BankRecord }) {
  return (
    <div className="rounded-[2px] border border-rule bg-field/50 p-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="num text-[10px] text-mute">{r.id}</span>
        <span className="num text-[13px] font-medium">{money(r.amount)}</span>
      </div>
      <div className="num mt-1 flex items-center gap-1.5 truncate text-[10px]">
        <span className="truncate" title={r.reference_number}>
          {r.reference_number || '— blank —'}
        </span>
        {r.ref_source === 'narration' && <Tag tone="ochre">from narration</Tag>}
      </div>
      <div className="num mt-1 break-all text-[10px] leading-snug text-slate">{r.narration}</div>
      <div className="mt-1 flex items-center gap-2">
        <span className="num text-[10px] text-mute">{shortDate(r.date)}</span>
        <Tag tone="slate">{r.type}</Tag>
      </div>
    </div>
  )
}

function Row({ label, value, tone = 'text-ink' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-mute">{label}</dt>
      <dd className={tone}>{value}</dd>
    </div>
  )
}

function EvidenceBlock({ evidence }: { evidence: Record<string, unknown> }) {
  const nearest = evidence.nearest_on_other_side as
    | { record: LedgerRecord | BankRecord; similarity: number; amount_delta: number; day_delta: number }
    | null
    | undefined

  const entries = Object.entries(evidence).filter(
    ([k]) => k !== 'nearest_on_other_side',
  )

  return (
    <div className="border-t border-rule">
      {nearest && (
        <div className="px-3 py-2.5">
          <div className="label mb-1">Closest thing on the other side</div>
          <div className="rounded-[2px] border border-dashed border-rule p-2">
            <div className="flex items-baseline justify-between">
              <span className="num text-[10px] text-mute">{nearest.record.id}</span>
              <span className="num text-[12px]">{money(nearest.record.amount)}</span>
            </div>
            <div className="num mt-1 truncate text-[10px]">
              {nearest.record.reference_number || '—'}
            </div>
            <div className="num mt-1 flex gap-3 text-[10px] text-mute">
              <span>{(nearest.similarity * 100).toFixed(0)}% alike</span>
              <span>{money(nearest.amount_delta)} apart</span>
              <span>{nearest.day_delta}d apart</span>
            </div>
          </div>
          <p className="mt-1.5 text-[11px] leading-snug text-slate">
            Shown because it is the nearest candidate, not because it is a match. The engine
            rejected it.
          </p>
        </div>
      )}

      {entries.length > 0 && (
        <details className="border-t border-rule">
          <summary className="label cursor-pointer px-3 py-1.5 hover:text-ink">
            Raw evidence
          </summary>
          <dl className="num space-y-0.5 px-3 pb-2.5 text-[10px]">
            {entries.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4">
                <dt className="text-mute">{k}</dt>
                <dd className="truncate text-right">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  )
}

function ActBtn({
  label,
  id,
  tone,
  onClick,
  busy,
}: {
  label: string
  id: string
  tone: 'pine' | 'oxblood' | 'ochre'
  onClick: () => void
  busy: boolean
}) {
  const styles = {
    pine: 'border-pine/40 text-pine hover:bg-pine hover:text-sheet hover:border-pine',
    oxblood: 'border-oxblood/40 text-oxblood hover:bg-oxblood hover:text-sheet hover:border-oxblood',
    ochre: 'border-ochre/40 text-ochre hover:bg-ochre hover:text-sheet hover:border-ochre',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label={`${label} ${id}`}
      className={`rounded-[3px] border bg-sheet px-2 py-1.5 text-[12px] font-medium transition-colors disabled:opacity-40 ${styles[tone]}`}
    >
      {label}
    </button>
  )
}
