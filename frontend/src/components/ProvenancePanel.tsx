import { useEffect, useState } from 'react'
import { ArrowRight, Check, Minus, X } from 'lucide-react'
import type { AnyRecord, Provenance } from '../lib/api'
import { api } from '../lib/api'
import { humanise, money, shortDate } from '../lib/format'
import { Pulse, SidePanel, Tag } from './bits'

/* ==========================================================================
   Why is this pair matched?

   Click any row on the canvas and this answers it: which pass caught it, what
   that rule asserts, which conditions it had to satisfy, and the actual numbers
   it satisfied them with. Plus which passes looked at the record first and
   declined it, because "pass 1 and 2 both said no" is part of the answer.

   Nothing here is reconstructed. Every field was recorded by the engine at the
   moment the rule fired.
   ========================================================================== */

const OUTCOME: Record<string, { label: string; tone: 'pine' | 'ochre' | 'oxblood' | 'slate' }> = {
  auto_resolved: { label: 'Resolved by rule', tone: 'pine' },
  proposed: { label: 'Proposed, awaiting you', tone: 'ochre' },
  flagged: { label: 'Flagged', tone: 'ochre' },
  unresolved: { label: 'No counterpart found', tone: 'oxblood' },
}

export function ProvenancePanel({
  runId,
  recordId,
  onClose,
}: {
  runId: string
  recordId: string
  onClose: () => void
}) {
  const [data, setData] = useState<Provenance | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    api
      .provenance(runId, recordId)
      .then((d) => live && setData(d))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [runId, recordId])

  const outcome = data ? OUTCOME[data.outcome] ?? OUTCOME.unresolved : null

  return (
    <SidePanel onClose={onClose} label={`Provenance for ${recordId}`}>
      <header className="sticky top-0 z-10 flex items-start justify-between gap-2 border-b border-rule bg-sheet px-3 py-2.5">
        <div>
          <div className="num text-[10px] text-mute">{recordId}</div>
          <h3 className="text-[14px] font-semibold">How this was resolved</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="-m-1.5 p-1.5 text-mute transition-colors hover:text-ink"
          aria-label="Close provenance"
        >
          <X size={16} />
        </button>
      </header>

      {error && <p className="px-3 py-3 text-[12px] text-oxblood">{error}</p>}
      {!data && !error && (
        <div className="space-y-2 p-3">
          <Pulse className="h-16" />
          <Pulse className="h-24" />
        </div>
      )}

      {data && (
        <>
          <div className="border-b border-rule px-3 py-2.5">
            <div className="flex items-center gap-2">
              {outcome && <Tag tone={outcome.tone}>{outcome.label}</Tag>}
              {data.rule && (
                <span className="num text-[10px] text-mute">pass {data.rule.pass}</span>
              )}
            </div>
            {data.rule && (
              <>
                <h4 className="mt-1.5 text-[13px] font-semibold">{data.rule.title}</h4>
                <p className="mt-0.5 text-[12px] leading-relaxed text-slate">
                  {data.rule.asserts}
                </p>
              </>
            )}
          </div>

          {/* the record itself */}
          <div className="border-b border-rule px-3 py-2.5">
            <div className="label mb-1.5">This record</div>
            <RecordCard r={data.record} />
          </div>

          {data.counterparts.length > 0 && (
            <div className="border-b border-rule px-3 py-2.5">
              <div className="label mb-1.5 flex items-center gap-1">
                Matched to <ArrowRight size={10} />
              </div>
              <div className="space-y-2">
                {data.counterparts.map((r) => (
                  <RecordCard key={r.id} r={r} />
                ))}
              </div>
            </div>
          )}

          {/* what the rule required, against what this pair had */}
          {data.rule && (
            <div className="border-b border-rule px-3 py-2.5">
              <div className="label mb-1.5">What the rule required</div>
              <ul className="space-y-1">
                {data.rule.requires.map((req) => (
                  <li key={req} className="flex items-start gap-1.5 text-[11px] leading-snug">
                    <Check size={11} className="mt-[2px] shrink-0 text-pine" />
                    <span>{req}</span>
                  </li>
                ))}
              </ul>

              {data.why.length > 0 && (
                <>
                  <div className="label mb-1.5 mt-3">What it actually found</div>
                  <ul className="num space-y-1">
                    {data.why.map((line) => (
                      <li key={line} className="text-[11px] leading-snug text-ink">
                        {line}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}

          {data.link && (
            <div className="border-b border-rule px-3 py-2.5">
              <dl className="num space-y-1 text-[11px]">
                <Row k="link" v={data.link.link_id} />
                <Row k="method" v={humanise(data.link.method)} />
                <Row k="engine confidence" v={data.link.confidence.toFixed(3)} />
                <Row
                  k="committed alone"
                  v={data.link.auto_resolved ? 'yes' : 'no — needs a human'}
                  tone={data.link.auto_resolved ? 'text-pine' : 'text-ochre'}
                />
              </dl>
            </div>
          )}

          {data.passes_that_declined.length > 0 && (
            <div className="border-b border-rule px-3 py-2.5">
              <div className="label mb-1.5">Passes that saw it first and declined</div>
              <div className="flex flex-wrap gap-1">
                {data.passes_that_declined.map((p) => (
                  <span
                    key={p}
                    className="num flex items-center gap-1 rounded-[2px] bg-bar px-1.5 py-[1px] text-[10px] text-slate"
                  >
                    <Minus size={8} /> {p}
                  </span>
                ))}
              </div>
              <p className="mt-1.5 text-[10px] leading-snug text-mute">
                Each pass runs only on what the previous ones left behind, so this record was
                offered to every one of these first.
              </p>
            </div>
          )}

          {data.exception && (
            <div className="px-3 py-2.5">
              <div className="label mb-1">
                Exception {data.exception.exception_id} · {humanise(data.exception.kind)}
              </div>
              <p className="text-[12px] leading-relaxed">{data.exception.engine_note}</p>
              {data.exception.llm && (
                <div className="mt-2 rounded-[2px] border border-rule bg-field/50 p-2">
                  <div className="label mb-1 flex items-center gap-1.5">
                    <span>The model</span>
                    <span className="num text-[9px] normal-case text-mute">
                      {data.exception.llm.model}
                    </span>
                    {data.exception.llm.source === 'mock' && <Tag tone="ochre">stand-in</Tag>}
                    {data.exception.llm.cached && <Tag tone="slate">cached</Tag>}
                  </div>
                  <p className="text-[11px] leading-relaxed">
                    {data.exception.llm.explanation}
                  </p>
                </div>
              )}
              {data.exception.decided_at && (
                <p className="num mt-2 text-[10px] text-slate">
                  decided {data.exception.status} at {data.exception.decided_at}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </SidePanel>
  )
}

function RecordCard({ r }: { r: AnyRecord }) {
  return (
    <div className="rounded-[2px] border border-rule bg-field/50 p-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="num text-[10px] text-mute">{r.id}</span>
        <span className="num text-[13px] font-medium">{money(r.amount)}</span>
      </div>
      <div className="num mt-1 truncate text-[10px]" title={r.reference_number}>
        {r.reference_number || '— blank —'}
      </div>
      <div className="mt-1 truncate text-[11px] text-slate">
        {'counterparty' in r ? r.counterparty : r.narration}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="num text-[10px] text-mute">{shortDate(r.date)}</span>
        <Tag tone="slate">{'status' in r ? r.status : r.type}</Tag>
        {'ref_source' in r && r.ref_source === 'narration' && (
          <Tag tone="ochre">ref from narration</Tag>
        )}
      </div>
    </div>
  )
}

function Row({ k, v, tone = 'text-ink' }: { k: string; v: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-mute">{k}</dt>
      <dd className={tone}>{v}</dd>
    </div>
  )
}
