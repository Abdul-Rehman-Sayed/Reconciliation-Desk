import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  Download,
  FlaskConical,
  Layers,
  Timer,
} from "lucide-react";
import type {
  Baselines,
  Calibration,
  Confusion,
  CostSplit,
  HoursSaved,
  Summary,
} from "../lib/api";
import { api } from "../lib/api";
import { humanise, pct } from "../lib/format";
import { CountUp, Panel, Pulse, Stat } from "./bits";
import { ThresholdPanel } from "./ThresholdPanel";

const FILL = {
  correct: "#0F7A5A",
  missed: "#D08A00",
  wrong: "#96271D",
};

type Props = {
  runId: string;
  summary: Summary;
  onRunChange: (runId: string) => void;
};

export function EvidenceScreen({ runId, summary, onRunChange }: Props) {
  const [confusion, setConfusion] = useState<Confusion | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [cost, setCost] = useState<{
    split: CostSplit;
    hours: HoursSaved;
  } | null>(null);
  const [baselines, setBaselines] = useState<Baselines | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const fail = (key: string) => (e: Error) =>
      live && setNotes((n) => ({ ...n, [key]: e.message }));

    Promise.allSettled([
      api
        .confusion(runId)
        .then((d) => live && setConfusion(d))
        .catch(fail("confusion")),
      api
        .calibration(runId)
        .then((d) => live && setCalibration(d))
        .catch(fail("calibration")),
      api
        .cost(runId)
        .then((d) => live && setCost({ split: d.split, hours: d.hours })),
      api
        .baselines(runId)
        .then((d) => live && setBaselines(d))
        .catch(fail("baselines")),
    ]).then(() => live && setLoading(false));

    return () => {
      live = false;
    };
  }, [runId]);

  return (
    <div className="mx-auto max-w-[1180px] px-4 py-6 sm:px-6">
      <header className="mb-4">
        <h2 className="text-[19px] font-semibold">The evidence</h2>
        <p className="mt-0.5 max-w-[86ch] text-[12px] leading-relaxed text-slate">
          A match rate on its own is a number, not a result. This page is what
          makes it a claim: what the classifier gets right per category, whether
          its confidence means anything, what the alternatives score, and what
          the whole thing cost. Every figure is computed from data already on
          disk — opening this page calls no model.
        </p>
      </header>

      {cost && <CostHeadline split={cost.split} hours={cost.hours} />}

      {baselines && <BaselinePanel baselines={baselines} />}

      <div className="mt-4 grid items-start gap-4 lg:grid-cols-2">
        {loading && !confusion ? <Pulse className="h-[340px]" /> : null}
        {confusion && <ConfusionPanel confusion={confusion} />}
        {loading && !calibration ? <Pulse className="h-[340px]" /> : null}
        {calibration && <CalibrationPanel calibration={calibration} />}
      </div>

      {(notes.confusion || notes.calibration) && !confusion && !calibration && (
        <div className="mt-4 flex items-start gap-2 rounded-[3px] border border-ochre/35 bg-ochre-soft px-3 py-2.5">
          <AlertTriangle size={15} className="mt-[2px] shrink-0 text-ochre" />
          <p className="text-[12px] leading-snug text-ink">
            {notes.confusion || notes.calibration}
          </p>
        </div>
      )}

      <ThresholdPanel
        runId={runId}
        summary={summary}
        onCommitted={onRunChange}
      />

      {cost && <AuditPanel runId={runId} hours={cost.hours} />}
    </div>
  );
}

function CostHeadline({
  split,
  hours,
}: {
  split: CostSplit;
  hours: HoursSaved;
}) {
  const projection = split.llm_only_projection;
  return (
    <div className="sheet p-5">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <div className="label">Records the model never saw</div>
          <div className="num mt-1 text-[clamp(38px,11vw,52px)] font-medium leading-[0.92] text-pine">
            <CountUp
              value={split.share_resolved_without_model}
              format={(n) => pct(n, 1)}
            />
          </div>
          <p className="num mt-1 text-[11px] text-slate">
            {split.records_never_seen_by_model.toLocaleString()} of{" "}
            {split.total_records.toLocaleString()} records
          </p>
        </div>

        <div className="flex flex-wrap gap-8">
          <Stat
            label="Hours of manual recon avoided"
            size="lg"
            hint={`${hours.manual_hours}h by hand, ${hours.hours_still_needed}h of queue left`}
          >
            <span className="text-pine">
              <CountUp
                value={hours.hours_saved}
                format={(n) => n.toFixed(1) + "h"}
              />
            </span>
          </Stat>
          <Stat
            label="Engine time, all six passes"
            size="lg"
            hint="deterministic layer only"
          >
            <span className="flex items-center gap-1.5">
              <Timer size={19} className="text-slate" />
              {split.engine_ms.toFixed(0)} ms
            </span>
          </Stat>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-rule pt-4 lg:grid-cols-4 lg:gap-x-8">
        <Stat
          label="Model calls made"
          size="sm"
          hint={`batched ${split.exceptions_requested} exceptions`}
        >
          {split.api_calls}
        </Stat>
        <Stat
          label="Served from cache"
          size="sm"
          hint={`${split.exceptions_served_from_cache} of ${split.exceptions_requested} exceptions`}
        >
          {pct(split.cache_hit_rate, 0)}
        </Stat>
        <Stat
          label="Tokens spent"
          size="sm"
          hint={
            split.tokens_measured
              ? "measured from the API response"
              : `nothing spent this run — a cold run would be ~${split.estimated_cold_tokens.toLocaleString()}`
          }
        >
          {split.tokens_measured ? (
            split.total_tokens.toLocaleString()
          ) : (
            <span className="text-pine">0</span>
          )}
        </Stat>
        <Stat label="Records per second" size="sm" hint="deterministic passes">
          {split.records_per_second?.toLocaleString() ?? "—"}
        </Stat>
      </div>

      {projection && (
        <div className="mt-4 rounded-[3px] border border-ochre/35 bg-ochre-soft/60 px-3 py-2.5">
          <div className="label mb-1 flex items-center gap-1.5 text-ochre">
            <Layers size={11} /> If the model decided everything instead
          </div>
          <p className="max-w-[92ch] text-[12px] leading-relaxed text-ink">
            Projected over all {split.total_records.toLocaleString()} records:{" "}
            <strong className="num font-semibold">
              ~{projection.projected_tokens_full_batch.toLocaleString()} tokens
            </strong>{" "}
            across ~{projection.projected_calls_full_batch} requests, taking
            around{" "}
            <span className="num">
              {projection.projected_seconds_full_batch.toFixed(0)}s
            </span>{" "}
            — about{" "}
            <strong className="num font-semibold">
              {projection.token_multiple ?? "—"}× what this run costs
            </strong>
            , and that is before it gets anything wrong.
          </p>
          <p className="mt-1.5 text-[11px] leading-snug text-slate">
            {projection.caveat} Multiple is {projection.token_multiple_basis}.
          </p>
        </div>
      )}
    </div>
  );
}

function BaselinePanel({ baselines }: { baselines: Baselines }) {
  const rows = [
    {
      ...baselines.naive,
      tone: "slate" as const,
      note: baselines.naive.description ?? "",
      cost: "free",
    },
    {
      name: "layered",
      label: "This engine, incl. proposals",
      precision: baselines.layered.precision,
      recall: baselines.layered.recall,
      f1: baselines.layered.f1,
      tone: "pine" as const,
      note: "Six deterministic passes, then the model on what survived them.",
      cost: `${baselines.layered.api_calls} call${baselines.layered.api_calls === 1 ? "" : "s"}`,
    },
    ...(baselines.llm_only && !baselines.llm_only.error
      ? [
          {
            ...baselines.llm_only,
            label: "Model decides everything *",
            tone: "ochre" as const,
            note: baselines.llm_only.description ?? "",
            cost: `${baselines.llm_only.total_tokens?.toLocaleString()} tokens / ${baselines.llm_only.records_sampled} records`,
          },
        ]
      : []),
  ];

  return (
    <Panel
      title="Against the two obvious alternatives"
      className="mt-4"
      right={
        <span className="num text-[10px] text-mute">
          {baselines.profile} dataset
        </span>
      }
    >
      <div className="scroll-x">
        <table className="w-full min-w-[540px]">
          <thead>
            <tr className="border-b border-rule">
              {["Approach", "Precision", "Recall", "F1", "Cost"].map((h, i) => (
                <th
                  key={h}
                  className={`label px-3 py-1.5 ${i === 0 ? "text-left" : "text-right"}`}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="greenbar">
            {rows.map((r) => (
              <tr key={r.name}>
                <td className="px-3 py-2">
                  <div className={`text-[12px] font-medium text-${r.tone}`}>
                    {r.label}
                  </div>
                  <div className="mt-0.5 max-w-[52ch] text-[11px] leading-snug text-slate">
                    {r.note}
                  </div>
                </td>
                <Cell value={r.precision} highlight={r.tone === "pine"} />
                <Cell value={r.recall} highlight={r.tone === "pine"} />
                <Cell value={r.f1} highlight={r.tone === "pine"} />
                <td className="num px-3 py-2 text-right text-[11px] text-mute">
                  {r.cost}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="border-t border-rule px-3 py-2">
        <p className="max-w-[100ch] text-[11px] leading-relaxed text-slate">
          The naive join is the floor — one equality match on reference and
          amount, which is what most teams already have in a spreadsheet. It
          finds every clean row and misses{" "}
          <span className="num">
            {(baselines.naive.false_negatives ?? 0).toLocaleString()}
          </span>{" "}
          real pairings: every fee deduction, every settlement delay, every
          damaged reference.
        </p>
        {baselines.llm_only && !baselines.llm_only.error && (
          <p className="mt-1.5 max-w-[100ch] text-[11px] leading-relaxed text-slate">
            * {baselines.llm_only.caveat} It found{" "}
            <span className="num">{baselines.llm_only.true_positives}</span> of{" "}
            <span className="num">{baselines.llm_only.expected ?? "—"}</span>{" "}
            pairs but proposed{" "}
            <span className="num text-oxblood">
              {baselines.llm_only.false_positives} wrong one
              {baselines.llm_only.false_positives === 1 ? "" : "s"}
            </span>
            . In reconciliation a wrong match is the expensive failure, because
            unlike a missed one it is silent.
          </p>
        )}
        {baselines.llm_only?.error && (
          <p className="mt-1.5 text-[11px] leading-snug text-ochre">
            LLM-only baseline not measured: {baselines.llm_only.error}
          </p>
        )}
      </div>
    </Panel>
  );
}

function Cell({
  value,
  highlight,
}: {
  value: number | null | undefined;
  highlight?: boolean;
}) {
  return (
    <td
      className={`num px-3 py-2 text-right text-[13px] ${
        highlight ? "font-medium text-pine" : ""
      }`}
    >
      {value === null || value === undefined ? "—" : pct(value, 2)}
    </td>
  );
}

function ConfusionPanel({ confusion }: { confusion: Confusion }) {
  const worst = [...confusion.by_category].sort((a, b) => a.f1 - b.f1)[0];

  return (
    <Panel
      title="What the classifier gets right, per category"
      right={
        <span className="num text-[10px] text-mute">
          {confusion.correct}/{confusion.scored} · macro F1{" "}
          {pct(confusion.macro_f1, 1)}
        </span>
      }
    >
      <div className="scroll-x">
        <table className="w-full min-w-[420px]">
          <thead>
            <tr className="border-b border-rule">
              {["Flaw", "Seen", "Precision", "Recall", "F1"].map((h, i) => (
                <th
                  key={h}
                  className={`label px-3 py-1.5 ${i === 0 ? "text-left" : "text-right"}`}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="greenbar">
            {confusion.by_category.map((c) => (
              <tr key={c.category}>
                <td className="px-3 py-1.5">
                  <div className="text-[12px]">{humanise(c.category)}</div>
                  {c.confused_with.length > 0 && (
                    <div className="text-[10px] leading-snug text-oxblood">
                      called{" "}
                      {c.confused_with
                        .map((x) => `${humanise(x.category)} ×${x.count}`)
                        .join(", ")}
                    </div>
                  )}
                </td>
                <td className="num px-3 py-1.5 text-right text-[12px] text-mute">
                  {c.support}
                </td>
                <td className="num px-3 py-1.5 text-right text-[12px]">
                  {pct(c.precision, 0)}
                </td>
                <td className="num px-3 py-1.5 text-right text-[12px]">
                  {pct(c.recall, 0)}
                </td>
                <td
                  className="num px-3 py-1.5 text-right text-[12px] font-medium"
                  style={{
                    color:
                      c.f1 === 1
                        ? FILL.correct
                        : c.f1 >= 0.8
                          ? FILL.missed
                          : FILL.wrong,
                  }}
                >
                  {pct(c.f1, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {confusion.resolved_before_the_model.length > 0 && (
        <div className="border-t border-rule bg-pine-soft/40 px-3 py-2.5">
          <div className="label mb-1 text-pine">
            Never reached the model —{" "}
            {confusion.cases_resolved_before_the_model} cases
          </div>
          <div className="flex flex-wrap gap-1">
            {confusion.resolved_before_the_model.map((d) => (
              <span
                key={d.ground_truth_category}
                className="num rounded-[2px] bg-sheet px-1.5 py-[1px] text-[10px] text-slate"
                title={`Would have been classified '${humanise(d.would_have_been)}'`}
              >
                {humanise(d.ground_truth_category)} {d.cases}
              </span>
            ))}
          </div>
          <p className="mt-1.5 max-w-[64ch] text-[11px] leading-relaxed text-ink">
            This table looks narrow because it is. A gateway fee and a
            settlement delay are resolved by a rule, with a proof, before
            anything is asked of a model — so they never appear in a classifier
            score. That absence is the architecture working, not a gap in the
            measurement.
          </p>
        </div>
      )}

      {worst && worst.f1 < 1 && (
        <p className="border-t border-rule px-3 py-2 text-[11px] leading-snug text-slate">
          Weakest category is {humanise(worst.category)} at {pct(worst.f1, 0)}{" "}
          F1 over {worst.support} case{worst.support === 1 ? "" : "s"} — small
          support, so treat it as a signal to watch rather than a measurement.
        </p>
      )}
    </Panel>
  );
}

function CalibrationPanel({ calibration }: { calibration: Calibration }) {
  const over = calibration.overconfidence;
  const populated = calibration.bins.filter((b) => b.n > 0);

  return (
    <Panel
      title="Does the confidence number mean anything?"
      right={
        <span className="num text-[10px] text-mute">
          ECE {calibration.expected_calibration_error.toFixed(3)}
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 px-3 py-3 sm:grid-cols-3">
        <Stat label="States on average" size="md">
          {pct(calibration.mean_confidence, 0)}
        </Stat>
        <Stat label="Actually right" size="md">
          <span className="text-pine">
            {pct(calibration.actual_accuracy, 0)}
          </span>
        </Stat>
        <Stat
          label={over > 0 ? "Overconfident by" : "Underconfident by"}
          size="md"
          hint={
            over > 0 ? "claims more than it earns" : "earns more than it claims"
          }
        >
          <span className={over > 0 ? "text-oxblood" : "text-pine"}>
            {pct(Math.abs(over), 0)}
          </span>
        </Stat>
      </div>

      <div className="border-t border-rule px-3 py-2.5">
        <div className="label mb-2">
          Stated confidence against measured accuracy
        </div>
        <div className="space-y-1.5">
          {populated.map((b) => (
            <div key={b.lower} className="flex items-center gap-2">
              <span className="num w-[62px] shrink-0 text-[10px] text-mute">
                {b.lower.toFixed(1)}–{b.upper.toFixed(1)}
              </span>
              <div className="relative h-[14px] flex-1 overflow-hidden rounded-[1px] bg-bar">
                <motion.div
                  className="absolute inset-y-0 left-0"
                  style={{ background: FILL.correct }}
                  initial={{ width: 0 }}
                  animate={{ width: `${(b.actual_accuracy ?? 0) * 100}%` }}
                  transition={{ duration: 0.5, ease: "easeOut" }}
                />
                <div
                  className="absolute inset-y-0 w-[2px] bg-ink"
                  style={{ left: `${b.stated_midpoint * 100}%` }}
                  title={`stated ~${pct(b.stated_midpoint, 0)}`}
                />
              </div>
              <span className="num w-[70px] shrink-0 text-right text-[10px]">
                {pct(b.actual_accuracy ?? 0, 0)}{" "}
                <span className="text-mute">n={b.n}</span>
              </span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[10px] leading-snug text-mute">
          Bar is measured accuracy. The dark tick is where the model claimed it
          would land. Bar past the tick means it was better than it said.
        </p>
      </div>

      <div className="border-t border-rule px-3 py-2.5">
        <div className="label mb-1">The number that matters</div>
        {calibration.high_confidence_n > 0 ? (
          <p className="text-[12px] leading-relaxed">
            Of the{" "}
            <span className="num font-medium">
              {calibration.high_confidence_n}
            </span>{" "}
            verdicts claiming 90% confidence or better,{" "}
            <span className="num font-medium text-pine">
              {pct(calibration.high_confidence_accuracy ?? 0, 0)}
            </span>{" "}
            were right. That is the figure an operator would actually act on.
          </p>
        ) : (
          <p className="text-[12px] leading-relaxed text-slate">
            Nothing claimed 90% or better on this run. For a model asked only
            about cases six deterministic passes could not settle, refusing to
            claim near-certainty is the correct behaviour — the confident cases
            were resolved before it was asked.
          </p>
        )}
        <p className="mt-1.5 text-[11px] leading-snug text-mute">
          {calibration.note}
        </p>
      </div>
    </Panel>
  );
}

function AuditPanel({ runId, hours }: { runId: string; hours: HoursSaved }) {
  const [summary, setSummary] = useState<{
    rows: number;
    by_event: Record<string, number>;
  } | null>(null);

  useEffect(() => {
    let live = true;
    api
      .auditSummary(runId)
      .then((d) => live && setSummary(d.summary))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [runId]);

  return (
    <Panel
      title="Audit log"
      className="mt-4"
      right={
        <a
          href={api.auditCsvUrl(runId)}
          download
          className="flex items-center gap-1.5 rounded-[3px] border border-rule bg-sheet px-2 py-1 text-[12px] transition-colors hover:border-ink"
        >
          <Download size={12} /> Export CSV
        </a>
      }
    >
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 px-3 py-3 sm:flex sm:flex-wrap sm:gap-x-8">
        {summary ? (
          Object.entries(summary.by_event).map(([event, n]) => (
            <Stat key={event} label={humanise(event)} size="sm">
              {n.toLocaleString()}
            </Stat>
          ))
        ) : (
          <Pulse className="h-9 w-full" />
        )}
      </div>
      <p className="border-t border-rule px-3 py-2 text-[11px] leading-relaxed text-slate">
        One row per decision, machine or human, with the rule that fired and
        what it asserted. Human actions are appended rather than overwriting the
        finding they decide — a reversal leaves both entries behind, which is
        the point of a log. Assumptions behind the {hours.hours_saved}h figure:{" "}
        {hours.assumptions.seconds_per_clean_match}s per clean line,{" "}
        {hours.assumptions.seconds_per_exception}s per exception.
      </p>
    </Panel>
  );
}

export { FlaskConical };
