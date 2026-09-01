export type PassStat = {
  name: string
  label: string
  description: string
  duration_ms: number
  links_made: number
  records_resolved: number
  exceptions_raised: number
  remaining_ledger: number
  remaining_bank: number
}

export type Summary = {
  run_id: string
  created_at: string
  source: string
  dataset_profile: string
  ledger_rows: number
  bank_rows: number
  total_records: number
  links_total: number
  links_auto: number
  links_proposed: number
  links_by_pass: Record<string, number>
  duplicates_flagged: number
  records_auto_resolved: number
  records_proposed: number
  records_unresolved: number
  match_rate_auto: number
  match_rate_with_proposed: number
  value_total: number
  value_auto_resolved: number
  value_rate_auto: number
  exceptions_total: number
  exception_records?: number
  accounting_overlap?: number
  exceptions_by_kind: Record<string, number>
  exceptions_needing_llm: number
  passes: PassStat[]
  llm_complete: boolean
  llm_stats: LlmStats | null
  decisions: Record<string, number>
  thresholds_changed: Record<string, number>
  adapter: AdapterNote | null
}

export type AdapterNote = {
  adapter: string
  label: string
  rows: number
  by_type: Record<string, number>
  settlement_batches: number
  date_range: [string, string]
  reference_field: string
  utr_note: string
}

export type LlmStats = {
  requested: number
  answered: number
  from_cache: number
  new_calls: number
  api_calls: number
  prompt_tokens: number
  completion_tokens: number
  tokens_saved_by_cache: number
  rate_limited: number
  bucket_wait_ms: number
  mode: 'mock' | 'groq'
  model: string | null
  budget: BudgetStatus
  errors: string[]
}

export type BudgetStatus = {
  day: string
  calls_made: number
  calls_budget: number
  calls_remaining: number
  prompt_tokens: number
  completion_tokens: number
}

export type CacheStats = {
  entries: number
  by_source: Record<string, number>
  path: string
  live_entries: number
  mock_mode: boolean
  prompt_version: string
}


export type ConfusionCategory = {
  category: string
  support: number
  correct: number
  predicted_as_this: number
  precision: number
  recall: number
  f1: number
  confused_with: { category: string; count: number }[]
}

export type Confusion = {
  run_id: string
  scored: number
  correct: number
  accuracy: number
  macro_f1: number
  unmapped: number
  clean_matches_that_reached_the_model: number
  resolved_before_the_model: {
    ground_truth_category: string
    would_have_been: string
    cases: number
  }[]
  cases_resolved_before_the_model: number
  labels: string[]
  matrix: Record<string, Record<string, number>>
  by_category: ConfusionCategory[]
  note: string
}

export type CalibrationBin = {
  lower: number
  upper: number
  n: number
  correct: number
  actual_accuracy: number | null
  stated_midpoint: number
  gap: number | null
}

export type Calibration = {
  run_id: string
  scored: number
  mean_confidence: number
  actual_accuracy: number
  overconfidence: number
  expected_calibration_error: number
  high_confidence_n: number
  high_confidence_accuracy: number | null
  bins: CalibrationBin[]
  note: string
}

export type CostSplit = {
  total_records: number
  records_never_seen_by_model: number
  records_seen_by_model: number
  share_resolved_without_model: number
  exceptions_requested: number
  exceptions_served_from_cache: number
  cache_hit_rate: number
  api_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  estimated_cold_tokens: number
  tokens_measured: boolean
  tokens_saved_by_cache: number
  engine_ms: number
  records_per_second: number | null
  mode: string | null
  model: string | null
  llm_only_projection?: {
    measured_on_records: number
    tokens_per_record: number
    projected_tokens_full_batch: number
    projected_seconds_full_batch: number
    projected_calls_full_batch: number
    token_multiple: number | null
    token_multiple_basis: string
    caveat: string
  }
}

export type HoursSaved = {
  assumptions: {
    seconds_per_clean_match: number
    seconds_per_exception: number
    working_day_hours: number
    basis: string
  }
  records: number
  manual_hours: number
  hours_still_needed: number
  hours_saved: number
  working_days_saved: number
  share_of_effort_removed: number
  records_auto_resolved: number
  exceptions_to_work: number
  note: string
}

export type CostResponse = {
  run_id: string
  split: CostSplit
  hours: HoursSaved
}

export type Baseline = {
  name: string
  label: string
  description?: string
  precision: number | null
  recall: number | null
  f1: number | null
  records?: number
  records_matched?: number
  match_rate?: number
  true_positives?: number
  false_positives?: number
  false_negatives?: number
  wall_seconds?: number
  cost?: string
  total_tokens?: number
  records_sampled?: number
  subsample_seed?: number
  proposed?: number
  expected?: number
  measured_at?: string
  model?: string | null
  error?: string | null
  caveat?: string
}

export type Baselines = {
  run_id: string
  profile: string
  naive: Baseline
  llm_only: Baseline | null
  layered: Baseline & {
    auto_precision: number | null
    auto_recall: number
    match_rate: number
    api_calls: number
    total_tokens: number
  }
}

export type ThresholdSpec = {
  key: string
  label: string
  unit: string
  min: number
  max: number
  step: number
  display?: string
  help: string
}

export type ThresholdInfo = {
  adjustable: ThresholdSpec[]
  defaults: Record<string, number>
  note: string
}

export type ThresholdPreview = {
  run_id: string | null
  derived_from: string
  committed: boolean
  thresholds: Record<string, number>
  changed: Record<string, number>
  summary: Summary
  accuracy: Accuracy | null
  delta: {
    match_rate_auto: number
    records_auto_resolved: number
    records_proposed: number
    records_unresolved: number
    exceptions_total: number
    links_auto: number
    precision?: number
    recall?: number
    auto_precision?: number
  }
  llm_coverage: {
    exceptions_needing_model: number
    already_cached: number
    would_need_new_calls: number
    would_cost_requests: number
    note: string
  }
  engine_ms: number
}

export type ProvenanceRule = {
  method: string
  pass: string
  title: string
  asserts: string
  requires: string[]
  auto: boolean
}

export type Provenance = {
  run_id: string
  record_id: string
  record: AnyRecord
  outcome: 'auto_resolved' | 'proposed' | 'flagged' | 'unresolved'
  link: Link | null
  exception: {
    exception_id: string
    kind: string
    engine_note: string
    engine_confidence: number
    needs_llm: boolean
    status: string
    llm: LlmVerdict | null
    decided_at: string | null
    decided_note: string | null
  } | null
  rule: ProvenanceRule | null
  why: string[]
  counterparts: AnyRecord[]
  passes_that_declined: string[]
}

export type AuditSummary = {
  rows: number
  by_event: Record<string, number>
  human_actions: number
  columns: string[]
}

export type ExplainDryRun = {
  run_id: string
  dry_run: true
  exceptions: number
  already_cached: number
  would_call_for: number
  would_cost_requests: number
  batch_size: number
  mode: 'mock' | 'groq'
  budget: BudgetStatus
}

export type CategoryScore = {
  category: string
  cases: number
  correct: number
  wrong_link: number
  missed: number
  duplicate_missed: number
  escaped_review: number
  auto: number
  proposed: number
  accuracy: number
}

export type Accuracy = {
  run_id: string
  validated_against: string
  caveat: string
  cases_total: number
  cases_correct: number
  case_accuracy: number
  pairs_expected: number
  pairs_proposed_by_engine: number
  true_positives: number
  false_positives: number
  false_negatives: number
  precision: number
  recall: number
  f1: number
  auto_pairs: number
  auto_true_positives: number
  auto_false_positives: number
  auto_precision: number
  by_category: CategoryScore[]
  false_positive_pairs: [string, string][]
  false_negative_pairs: [string, string][]
}

export type LedgerRecord = {
  id: string
  side: 'ledger'
  date: string
  amount: number
  reference_number: string
  counterparty: string
  payment_method: string
  status: string
}

export type BankRecord = {
  id: string
  side: 'bank'
  date: string
  amount: number
  reference_number: string
  narration: string
  type: string
  ref_source: string
}

export type AnyRecord = LedgerRecord | BankRecord

export type Link = {
  link_id: string
  ledger_ids: string[]
  stmt_ids: string[]
  pass_name: string
  method: string
  confidence: number
  auto_resolved: boolean
  evidence: Record<string, unknown>
}

export type LlmVerdict = {
  category: string
  explanation: string
  confidence: number
  suggested_action: 'approve' | 'reject' | 'investigate'
  source: 'groq' | 'mock' | 'unavailable'
  model: string | null
  cached?: boolean
}

export type ExceptionRow = {
  exception_id: string
  kind: string
  ledger_ids: string[]
  stmt_ids: string[]
  engine_confidence: number
  engine_note: string
  needs_llm: boolean
  link_id: string | null
  evidence: Record<string, unknown>
  llm: LlmVerdict | null
  status: 'pending' | 'approved' | 'rejected' | 'investigating'
  decided_at: string | null
  decided_note: string | null
  ledger_records?: LedgerRecord[]
  bank_records?: BankRecord[]
}

export type ExceptionPage = {
  run_id: string
  total: number
  page: number
  page_size: number
  pages: number
  items: ExceptionRow[]
  facets: {
    kind: Record<string, number>
    category: Record<string, number>
    status: Record<string, number>
    suggested_action: Record<string, number>
  }
}

export type DatasetInfo = {
  profile: string
  seed: number
  cases: number
  ledger_rows: number
  bank_rows: number
  categories: Record<string, number>
}

export type Health = {
  status: string
  mock_mode: boolean
  groq_key_present: boolean
  groq_reachable: boolean
  groq_model?: string
  groq_error?: string
  cache: CacheStats
  budget: BudgetStatus
}

export type ReconcileResult = {
  run_id: string
  summary: Summary
  accuracy: Accuracy | null
  reused: boolean
  adapter: AdapterNote | null
}

const BASE = '/api'

class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(
      'Could not reach the backend. Start it with:  uvicorn app.main:app --reload  in /backend',
      0,
    )
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
    }
    throw new ApiError(detail, res.status)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => req<Health>('/health'),
  datasets: () => req<{ datasets: DatasetInfo[] }>('/datasets'),

  reconcileBundled: (dataset: string, force = false) =>
    req<ReconcileResult>(
      `/reconcile?dataset=${encodeURIComponent(dataset)}&force=${force}`,
      { method: 'POST' },
    ),

  reconcileUpload: (ledger: File, bank: File) => {
    const form = new FormData()
    form.append('ledger', ledger)
    form.append('bank_statement', bank)
    return req<ReconcileResult>('/reconcile', { method: 'POST', body: form })
  },

  explain: (runId: string) =>
    req<{ run_id: string; llm_stats: LlmStats; explained: number; already_done: boolean }>(
      `/runs/${runId}/explain`,
      { method: 'POST' },
    ),

  explainDryRun: (runId: string) =>
    req<ExplainDryRun>(`/runs/${runId}/explain?dry_run=true`, { method: 'POST' }),

  confusion: (runId: string) => req<Confusion>(`/runs/${runId}/confusion`),
  calibration: (runId: string) => req<Calibration>(`/runs/${runId}/calibration`),
  cost: (runId: string) => req<CostResponse>(`/runs/${runId}/cost`),
  baselines: (runId: string) => req<Baselines>(`/runs/${runId}/baselines`),
  provenance: (runId: string, recordId: string) =>
    req<Provenance>(`/runs/${runId}/provenance/${encodeURIComponent(recordId)}`),
  auditSummary: (runId: string) =>
    req<{ run_id: string; summary: AuditSummary; rows: Record<string, unknown>[] }>(
      `/runs/${runId}/audit`,
    ),
  auditCsvUrl: (runId: string) => `${BASE}/runs/${runId}/audit?format=csv`,

  thresholds: () => req<ThresholdInfo>('/thresholds'),

  previewThresholds: (runId: string, overrides: Record<string, number>) =>
    req<ThresholdPreview>(`/runs/${runId}/thresholds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides, commit: false }),
    }),

  commitThresholds: (runId: string, overrides: Record<string, number>) =>
    req<ThresholdPreview>(`/runs/${runId}/thresholds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides, commit: true }),
    }),

  summary: (runId: string) => req<Summary>(`/runs/${runId}/summary`),
  accuracy: (runId: string) => req<Accuracy>(`/runs/${runId}/accuracy`),
  records: (runId: string) =>
    req<{ ledger: LedgerRecord[]; bank: BankRecord[]; links: Link[] }>(`/runs/${runId}/records`),

  exceptions: (runId: string, params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== '') q.set(k, String(v))
    }
    return req<ExceptionPage>(`/runs/${runId}/exceptions?${q.toString()}`)
  },

  act: (runId: string, exceptionId: string, action: string, note?: string) =>
    req<{ exception: ExceptionRow; decisions: Record<string, number> }>(
      `/runs/${runId}/exceptions/${exceptionId}/action`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, note: note || null }),
      },
    ),
}

export { ApiError }
