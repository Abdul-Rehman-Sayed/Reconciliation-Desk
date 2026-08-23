# Reconciliation Desk

**AI Finance Controller — Reconciliation Agent · Razorpay AI Buildathon 2026, Track 04**

---

## The problem

A merchant's internal ledger and their bank or payment-gateway statement are two
independent records of the same money, and they never agree. Settlements land a day
or three late. The gateway takes its fee before the money arrives, so the amounts
differ by 2.36%. A webhook fires twice and the ledger books the same payment twice.
Four payments settle as one batched credit whose reference matches none of them.
Somebody's reference number gets truncated in a bank export. A refund reverses a
payment a week after the fact. And occasionally there is a credit in the bank with
nothing behind it at all, or a payment the ledger swears was captured that the bank
never saw. Today a person works through all of it by hand, every month. This project
automates the part that is mechanical, explains the part that is not, and refuses to
touch the money either way.

---

## The three layers

**1 · A deterministic engine that does the heavy lifting.** Plain Python, no AI, in
[`backend/app/matching.py`](backend/app/matching.py). Six passes run in order, each
working only on what the previous ones left behind: exact reference/amount/date
matching; a tolerant pass that understands gateway-fee formulas, T+3 delays and
single-character reference drift; reversal pairing; same-side duplicate detection;
composite matching for N-rows-settle-as-one; and fuzzy reference scoring. On the
bundled data this resolves **90%** of all records on its own, in about 25
milliseconds, with no model involved. Every threshold it uses is a named constant at
the top of the module, not a magic number buried in a conditional.

**2 · An LLM that only sees what survived.** [`backend/app/llm.py`](backend/app/llm.py)
calls Groq, and it is asked about **32 of 856 records** — under 4% of the batch. It
never sees a clean match. Its job is narrow and it is held to it by a JSON schema:
classify the exception, explain the likely cause in language an ops person can read,
propose an action, and return its own confidence. It classifies and explains. It does
not resolve. The prompt makes *"there is genuinely nothing to match this to"* a
first-class answer, because inventing a plausible counterpart for an orphan is the
most damaging thing it could do here.

**3 · A human gate that nothing gets past.** Every exception lands in a queue with
approve / reject / investigate. There is no auto-execute path in the codebase — the
only thing `POST /exceptions/{id}/action` does is record what a person decided. The
agent's opinion is an opinion sitting in a queue.

The split is visible in the API on purpose: `POST /api/reconcile` runs the
deterministic passes and returns in ~100ms; `POST /api/runs/{id}/explain` is a
separate call that sends the remainder to Groq. The UI runs them in that order, so
what you watch on screen is the order the work actually happened in.

---

## Results

Every number below is computed by the code, from a real comparison it performed.
Reproduce them with `python scripts/evaluate.py` and `--profile stress`.

| | Standard set | Adversarial set |
|---|---|---|
| Records reconciled | 856 | 877 |
| **Auto-resolved by rule** (no human, no model) | **90.19%** (772) | **83.01%** (728) |
| Including model-proposed, awaiting approval | 99.07% (+76) | 99.09% (+141) |
| Left genuinely unresolved | 8 | 8 |
| Exceptions raised — *groups, see below* | 52 | 86 |
| — the records those groups cover | 104 | 169 |
| — of those groups, sent to the LLM | 32 | 66 |
| Deterministic engine time | 23 ms | 32 ms |
| Case accuracy vs ground truth | 100.00% | 100.00% |
| Pair precision / recall | 100% / 100% | 100% / 100% |
| **Auto-resolve precision** | **100.00%** | **100.00%** |

The three record rows partition the batch and nothing else does: 772 + 76 + 8 = 856,
and 728 + 141 + 8 = 877. **An exception is a group of records, not a record**, so the
queue size is a different unit and adding it to the split is a category error — 52
exceptions cover 104 records on the standard set. The two are printed in separate
blocks by `scripts/evaluate.py` for that reason, and `summary.exception_records`
carries the record figure in the API. Duplicates compound it: a flagged duplicate is
counted as auto-resolved *and* raises an exception, because the rule resolved it and a
person should still see it. The partition is asserted against both bundled datasets in
`tests/test_matching.py`, and `summary.accounting_overlap` reports a violation rather
than absorbing one.

**The number that matters is the first one, and the second one is why.** The gap
between 83% and 99% on the adversarial set is 141 records the engine found a
candidate for and deliberately refused to commit. Harder data does not make this
system wrong — it makes it ask for more help. That is the behaviour you want from
anything standing between you and a payments ledger.

### Against the two obvious alternatives

A match rate with nothing beside it is a number, not a result. Both baselines are
measured, not asserted — `python scripts/baseline.py`.

| Approach | Precision | Recall | Cost |
|---|---|---|---|
| Naive exact reference + amount join | 100% | **76.64%** | free |
| **This engine, incl. proposals** | **100%** | **100%** | 3 requests |
| Model decides every pairing * | **95.00%** | 100% | ~7× the tokens |

The naive join is the floor — the twenty lines of pandas most finance teams already
have in a spreadsheet. It finds every clean row and misses 100 real pairings: every
fee deduction, every settlement delay, every damaged reference.

The LLM-only baseline is the ceiling nobody should want. It found every real pair on
the subsample **and proposed one that does not exist.** In reconciliation a wrong
match is the expensive failure, because unlike a missed one it is silent — the books
balance, the queue is empty, and nobody finds out until an auditor does.

\* Measured once on a fixed 40-record subsample (seed 4242) and frozen to
`data/baselines/`. Running it over the full 856 records would spend precisely what
the layered design exists to avoid — and "we avoided this cost" is not a claim you
can support by paying it. Every figure projected from it is labelled a projection
wherever it appears.

### What the classifier is measured on, and what it is not

Precision and recall per flaw category rather than one blended number, scored against
the injected flaw — `GET /api/runs/{id}/confusion`.

The matrix is deliberately narrow, and that is the argument rather than a gap in the
testing: **120 of the 400 cases never reach the model at all.** A gateway fee, a
settlement delay, a double webhook fire and a reversal are each resolved by a rule,
with a proof, before anything is asked of an LLM. Only split payments, damaged
references and genuine orphans get that far.

Confidence is checked too — `GET /api/runs/{id}/calibration` bins stated confidence
against measured accuracy and reports the gap. The figure worth quoting is the top
bin: of the verdicts claiming ≥90% confidence, what fraction were actually right. A
confidence score nobody has checked is decoration.

### What it saves

10.7 hours of manual reconciliation on 856 records becomes 3.5 hours of exception
queue — **7.2 hours, about one working day per batch.** The queue is counted as real
work rather than assumed free, which is why the figure is lower than the match rate
alone would suggest. Both assumptions (45s per clean line, 4 min per exception) are
exposed as parameters so they can be argued with rather than taken on trust.

### About the 100%

We generated the data and we wrote the matcher, so the answer key is not independent
and a 100% off a single dataset would be worth very little. Two things were done
about that, and neither one fully fixes it:

- **A cross-seed holdout** — `python scripts/holdout.py`, seeds pinned in the file.
  The engine is scored on 9 datasets per profile generated from seeds never used
  while tuning thresholds. Standard: mean case accuracy 1.0000 (min 1.0000).
  Adversarial: mean 0.9997 (min 0.9975 — that is 399 of 400 cases). Auto-resolve
  precision never dropped below 1.0000 on any of the 18 runs. So the thresholds are
  not fitted to one draw.

  Case accuracy here moves by a case or two with the seed set, in both directions:
  a second draw of nine seeds gives 0.9986 standard and 0.9992 adversarial. Quoting
  either profile as a flat 1.0000 would be quoting the luckiest draw, so the pinned
  seeds are part of the claim and the script prints all of them. The figure that
  does hold across every draw is auto-resolve precision at 1.0000, which is the one
  worth holding: a missed match becomes a queue item, a wrong auto-match becomes a
  silently balanced set of books.
- **An adversarial dataset the thresholds were not designed around.** `--stress`
  keeps every category the brief specifies and spends 12 points of the clean share on
  four that break specific assumptions: references truncated so far that two ledger
  rows fit equally well, settlements that come up 8–30% short by no fee formula,
  statements whose reference column is blank, and T+8-to-T+12 delays outside every
  date window. The first run against it scored **0/12 on ambiguous decoys and 0/12 on
  late settlements** and exposed two genuine bugs (below).

What this still does not prove: real bank exports are messier than anything we
thought to generate, and the failure modes we did not imagine are exactly the ones
not in here. On uploaded files the app shows no accuracy score at all, deliberately —
there is no answer key for real data, and the honest measures there are the
auto-resolve rate and the size of the queue.

### Three things measurement changed

Kept here because "we tried it and measured it" is the useful part.

1. **An over-permissive composite fallback, deleted.** Brute-forcing subset sums on
   amount alone, allowing a fee-shaped remainder, made four unrelated ledger rows
   totalling ₹25,592.23 look like a ₹25,183.69 payout "minus a 1.6% fee". It stole
   rows from two real batches, so one bad rule broke three cases. Across ~1,450
   candidate subsets per bank row a coincidence is reliably available. It is now off
   behind `COMPOSITE_ALLOW_AMOUNT_ONLY = False`, left in the file with the reasoning.
2. **A tolerance band that could not discriminate.** Two candidates ₹2 apart both
   fell inside the ±0.4% band and therefore scored *identically*, so the engine chose
   between them by luck — and lost, 0/12, cross-matching every decoy pair. Amount
   proximity is now continuous, so an exact match outranks a merely in-band one.
3. **Date windows are not what makes a match safe — uniqueness is.** T+11
   settlements were being missed because they sat outside every window. When exactly
   one unmatched row on each side carries the same reference and the amounts agree to
   the paisa, the date gap carries no information. That rule is allowed to ignore the
   windows precisely because it demands uniqueness on both sides.

---

## Running it

Two terminals. Nothing here costs money; there are no paid tiers, fonts or icons.

### Backend

```bash
cd backend
pip install -r requirements.txt

cp .env.example .env          # then paste your Groq key into it
python scripts/check_groq.py  # verifies key, model list and one real structured call

uvicorn app.main:app --reload --port 8000
```

Get a free key at [console.groq.com/keys](https://console.groq.com/keys) — no credit
card. **The app runs without one.** The deterministic engine needs no key and is the
part doing the work; exceptions that would have gone to the model are marked
`source: "unavailable"` in the API and *"model unavailable"* in the UI, rather than
filled in with something invented.

#### Building against the stand-in

`USE_MOCK_LLM=true` returns a rule-based stand-in instead of calling Groq. It needs
no key, never touches the network, and costs nothing — use it for all interface work.
Every verdict it produces is stamped `source: "mock"` and the UI labels it as a
stand-in, because a templated sentence sitting under a heading that says *what the
model thinks* without that stamp would be the most dishonest thing in this repo.

It writes to a **separate cache file** from the real one. A mock answer in the live
cache would be served silently on the next real run and never re-asked, which would
turn a flag meant to save quota into the thing that fakes the demo.

No model name is hardcoded as a requirement. `resolve_model()` fetches
`GET /openai/v1/models` live and takes the first entry from a preference list that
Groq is actually serving that day, because the free lineup changes. Override with
`GROQ_MODEL=` in `.env` and it will be checked against the live list before use.

### Token discipline

The free tier is a **token bucket, not a daily allowance**, and that distinction is
the whole story. Measured off this account's live response headers
(`python scripts/check_limits.py`, which reads them rather than trusting the docs):
`openai/gpt-oss-20b` gives 1,000 requests/day and **8,000 tokens/minute**.

The first version sent one request per 5 exceptions, three at a time, with
`max_tokens=2000`. Groq reserves `max_tokens` against the bucket *at admission*, so
three concurrent calls asked for 3 × (1,060 prompt + 2,000 reserved) = 9,180 tokens
against a bucket of 8,000. Four of seven calls came back 429. **The completion length
was never the problem — the reservation was.**

What changed, in order of how much each actually saved:

1. **A disk cache keyed by a hash of the exact fields sent.** After the bundled
   dataset has been run once, every later run of *those same exceptions* is free.
   This is what makes it safe to rehearse a demo thirty times. It is committed to
   the repo on purpose — those verdicts were paid for once, and a fresh clone
   should not pay for them twice.

   **What is actually in the committed cache today: 12 verdicts, one batch.** That
   covers 12 of the standard set's 32 exceptions and none of the adversarial set's
   66. So a cold *real* run is not free — it is 2 requests on standard and 6 on
   adversarial, against a 1,000/day allowance. Stated rather than rounded to "free"
   because a demo that assumes zero and needs eight is a demo that discovers its
   own rate limit on stage. `USE_MOCK_LLM=true` (the committed default) reads a
   separate cache that does cover both sets in full, at no cost and no network.
2. **A client-side token bucket mirroring Groq's own.** Requests wait for capacity
   here rather than being rejected there. A 429 costs a request out of the daily
   allowance and returns nothing; waiting 400 ms costs nothing.
3. **12 exceptions per request** instead of 5 — a cold run on the standard set is 3
   requests, not 7.
4. **Only the fields a verdict turns on**, as compact JSON with positional rows. Cut
   the per-exception payload from ~210 tokens to ~70.
5. **`max_tokens` budgeted per record**, so the reservation tracks the work asked for.
6. **A hard local daily budget** (`GROQ_DAILY_CALL_BUDGET`) tracked on disk, which
   refuses to call past a ceiling you set — it does not depend on Groq being the one
   to say no.

Ask what a run would cost before spending it:

```bash
curl -X POST 'localhost:8000/api/runs/{id}/explain?dry_run=true'
# real mode:  {"exceptions": 32, "already_cached": 12, "would_call_for": 20, "would_cost_requests": 2}
# mock mode:  {"exceptions": 32, "already_cached": 32, "would_call_for":  0, "would_cost_requests": 0}
```

Re-running the same batch is idempotent: identical inputs at identical thresholds
return the existing run rather than rebuilding one, and its verdicts come back with
it. The engine is deterministic, so this is not a cache trick — it is declining to
recompute a value we already hold.

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Vite proxies `/api` to `127.0.0.1:8000`, and FastAPI also sets CORS for `:5173`, so
either path works.

### Tests and evaluation

```bash
cd backend
python -m pytest tests/ -q                     # 78 tests: matching tiers + phase 2
python scripts/evaluate.py                     # engine + accuracy, standard set
python scripts/evaluate.py --profile stress -v  # adversarial set, every imperfect case
python scripts/baseline.py                     # naive vs layered, free
python scripts/holdout.py                      # 9 unseen seeds per profile, free
python scripts/baseline.py --llm               # measure the LLM-only baseline once
python scripts/check_limits.py                 # this account's real Groq rate limits
python scripts/migrate_cache.py --write        # recover paid-for verdicts after a payload change
python scripts/generate_data.py --seed 99 --stress --out data/stress   # fresh batch
```

Nothing in the test suite makes a network call. Several tests assert that: mock mode
monkeypatches `requests.post` to raise, and the budget test asserts that a spent
budget refuses to call rather than failing open.

---

## What's in the repo

```
backend/
  app/
    matching.py     the six passes, Thresholds, no AI in this file
    datagen.py      synthetic generator; both flaw mixes live at the top
    scoring.py      grades the engine against ground truth, after the fact
    llm.py          Groq client: schema-forced JSON, batching, token bucket, disk cache
    mockllm.py      rule-based stand-in for USE_MOCK_LLM
    analytics.py    confusion matrix, calibration, cost split, hours saved
    baselines.py    naive join (free) and the frozen LLM-only measurement
    audit.py        provenance lookups and the exportable audit log
    adapters/       razorpay.py — settlement recon report column mapping
    dataio.py       CSV loading, shape validation, adapter detection
    store.py        one JSON file per run, plus fingerprint lookup for idempotent re-runs
    main.py         FastAPI
  data/
    standard/       ledger.csv · bank_statement.csv · ground_truth.json   (seed 20260822)
    stress/         same three files, adversarial mix                     (seed 4242)
    baselines/      the frozen LLM-only measurement, committed
    llm_cache.json  real Groq verdicts, committed — 12 of them, so a cold real
                    run on the standard set is 2 requests, not 0
  scripts/          generate_data · evaluate · baseline · holdout · check_groq ·
                    check_limits · migrate_cache
  tests/            test_matching.py · test_phase2.py
docs/
  ADR-001-layered-reconciliation.md   rules vs ML vs LLM vs layered, one line of tradeoff each
frontend/
  src/components/   StartScreen · Processing · SummaryScreen · EvidenceScreen ·
                    MatchCanvas · ThresholdPanel · ProvenancePanel ·
                    ExceptionsScreen · ExceptionDetail · bits (shared primitives)
  src/lib/          api (typed client) · format · useIsNarrow
```

The engine never reads `ground_truth.json`. Scoring loads it separately, after the
run, in a module the engine does not import.

### The data

~400 ground-truth cases become two CSVs plus an answer key. Amounts are INR, plain
numbers, no symbol. Flaw mix, per the brief:

| Category | Standard | What it tests |
|---|---|---|
| Clean exact match | 60% | pass 1 |
| Date-shifted (T+1..T+3) | 15% | date tolerance |
| Fee-deducted | 8% | fee-aware amount matching |
| Duplicate ledger entry | 5% | same-side duplicate detection |
| Split / batched | 4% | composite many-to-one → LLM |
| Reference typo / truncation | 4% | fuzzy matching, confidence scoring |
| Refund / reversal pair | 2% | net-to-zero pairing |
| Orphan bank entry | 1% | stays unresolved |
| Orphan ledger entry | 1% | stays unresolved |

The adversarial mix keeps all of the above and adds ambiguous decoys, short
settlements, narration-only references and late settlements at 3% each, taken out of
the clean share.

The two orphan categories are the honest floor: 8 records that stay unresolved on
both datasets, after the LLM has looked at them. Orphan ledger rows carry status
`captured`, not `failed` — a status flag would have made them trivially rule-solvable
and dodged the point.

---

## API

| | |
|---|---|
| `GET /api/health` | includes a live Groq reachability check |
| `GET /api/datasets` | the bundled datasets, described from the files themselves |
| `GET /api/models` | Groq's live model list and which one was selected |
| `POST /api/reconcile?dataset=standard\|stress` | deterministic passes only; or upload two CSVs |
| `POST /api/runs/{id}/explain` | sends the surviving exceptions to Groq |
| `GET /api/runs/{id}/summary` | counts, both match rates, per-pass timings |
| `GET /api/runs/{id}/accuracy` | scored against ground truth; **404 for uploads, by design** |
| `GET /api/runs/{id}/exceptions` | paginated, filterable by kind / category / confidence / state |
| `POST /api/runs/{id}/exceptions/{id}/action` | the human gate |
| `GET /api/runs/{id}/records` | both sides plus every link, for the two-column view |
| `GET /api/runs` | the most recent runs, newest first |
| `GET /api/runs/{id}` | the whole stored run, exactly as it sits on disk |

Added in phase 2. **Every one of these is free except the last**, which is why the
last one is a button a person presses and not something a slider triggers:

| | |
|---|---|
| `POST /api/runs/{id}/explain?dry_run=true` | what explaining would cost, without spending it |
| `GET /api/runs/{id}/confusion` | per-category precision/recall for the classifier |
| `GET /api/runs/{id}/calibration` | stated confidence against measured accuracy |
| `GET /api/runs/{id}/cost` | the cost/latency split and hours of manual work avoided |
| `GET /api/runs/{id}/baselines` | naive join and the frozen LLM-only measurement |
| `GET /api/runs/{id}/provenance/{record_id}` | which pass, which rule, what evidence |
| `GET /api/runs/{id}/audit?format=csv\|json` | every decision, machine and human |
| `GET /api/thresholds` | what a slider may move, with units and bounds |
| `POST /api/runs/{id}/thresholds` | deterministic re-run at new tolerances |
| `POST /api/runs/{id}/thresholds/explain` | commits a threshold change, then explains what is newly unexplained — **the one route here that can spend**, which is why it is a separate button |

`POST /api/runs/{id}/thresholds` deserves a note. Threshold changes only move the
deterministic layer's classification, and re-classification is pure computation — the
whole batch re-runs in ~25 ms. **There is no code path from that route to Groq.** It
is not that we chose not to call the model; the route cannot. What it *does* report is
coverage: how many exceptions at the new settings already have a cached verdict and
how many would need a fresh call, shown before anything could be spent. Explaining a
new setting is a separate, deliberate button.

This matters because the person dragging the slider during a demo is an audience
member, and an audience member should not be able to spend quota.

---

## Interface notes

The subject is an audit desk, so the surface is modelled on green-bar
continuous-form ledger paper — the alternating pale stripe accounting stock has been
printed on for decades. Neutrals run cool green-grey rather than warm cream. Every
figure is set in IBM Plex Mono with tabular figures, so digits stack in columns the
way they have to when you are tying out a statement. Type is the IBM Plex superfamily
throughout (Sans for prose, Mono for data, Condensed for column heads), all
open-source.

The signature element is the two-column view: ledger on the left, statement on the
right, with a line drawn for every pair the engine actually committed — coloured by
the pass that found it and positioned by the real row index of both records. Both
columns are date-sorted, so a healthy run reads as a near-diagonal weave and **the
rows with no line attached are the exceptions**. During processing the lines draw
themselves pass by pass, which is the architecture made literal.

The processing sequence is paced so it can be read — the engine finishes in ~25ms,
which is too fast to see. The real millisecond figure for each pass is printed next
to it precisely so the pacing cannot be mistaken for the timing.

The status-tile colours in the accuracy chart are re-stepped from the same hues as
the text tokens and validated for lightness, chroma, CVD separation and
normal-vision separation against the sheet colour — the text ink reads too gray as a
fill, and the original oxblood/ochre pair failed the adjacent-pair check at ΔE 13.8.

---

## Razorpay settlement reports

`backend/app/adapters/razorpay.py` maps Razorpay's settlement recon report onto the
engine's ledger shape. Column list verified against the current docs for
`GET /v1/settlements/recon/combined` (checked August 2026, because this schema moves):
`entity_id`, `type`, `debit`, `credit`, `amount`, `currency`, `fee`, `tax`, `on_hold`,
`settled`, `created_at`, `settled_at`, `settlement_id`, `description`, `notes`,
`payment_id`, `settlement_utr`, `order_id`, `order_receipt`, `method`, `card_network`,
`card_issuer`, `card_type`, `dispute_id`.

Drop one on the upload screen and it is detected by its column set — no flag to set.
Three things the adapter has to get right that are easy to miss: **amounts are in
paise** (a tool that reads them as rupees is out by 100× and matches nothing),
**timestamps are Unix integers** and it is `settled_at` that matters (`created_at` is
usually days earlier, and matching on it manufactures a settlement delay that never
happened), and **`type` decides the sign** — `payment`/`transfer` in, `refund`/
`adjustment` out — because the reversal pass keys off negative amounts.

### The UTR problem

The obvious reconciliation key is `settlement_utr`, and it does not work.

Razorpay assigns a UTR to the settlement it initiates. The money then travels through
the banking system, and the correspondent bank that finally credits the merchant
account issues **its own** reference, which is what lands in the bank statement
narration. They are different strings for the same movement of money. Joining
Razorpay's `settlement_utr` against the statement narration finds nothing — and the
natural next move, loosening the match until something sticks, is exactly how a
reconciliation system starts producing confident wrong answers.

What works is two-level:

- **Batch to bank.** The bank credit matches the settlement *batch*, on amount and
  settlement date. One bank line, one `settlement_id`. This is the join the statement
  can actually support.
- **Transaction to batch.** Each transaction matches its batch on `settlement_id`
  within the report, then is identified by `order_id` or `payment_id`. This join never
  touches the bank statement at all.

So it is batch-to-bank, then transaction-to-batch — not the flat 1:1 join almost every
naive implementation assumes. The adapter therefore uses `order_id` as the reference
key and carries the UTR through for display only, and `settlement_batches()` exposes
the batch grouping that level one needs. Rearchitecting the matching engine around
two-level settlement is real work and is listed under known limits rather than
half-done here.

---

## Known limits

- Ground-truth accuracy exists only for the two bundled synthetic datasets. Uploads
  get no score, and the API returns 404 on `/accuracy` with an explanation rather
  than a fabricated number.
- Composite matching relies on mining the counterparty out of the bank narration. A
  batch settlement whose narration names no recognisable party will not be found —
  the amount-only fallback that would have caught it cost more precision than it was
  worth (see above).
- `COMPOSITE_MAX_GROUP = 4`. A batch of five or more ledger rows is not searched.
- The date windows are calibrated for Indian PG settlement. A different rail would
  need them re-tuned; they are named constants at the top of the module for exactly
  that reason.
- Duplicate detection treats the earliest of two indistinguishable rows as the
  original. When two rows are genuinely identical that choice is arbitrary — the rule
  exists so the answer is *consistent*, not because one answer is more correct.
- The Razorpay adapter maps columns correctly but the engine still runs its normal
  flat passes over the result. True two-level settlement matching (batch-to-bank, then
  transaction-to-batch) is described above and not implemented.
- The LLM-only baseline is one measurement on 40 records, so its 95% precision figure
  carries the uncertainty of a single small sample. It is enough to show the shape of
  the tradeoff; it is not a benchmark.
- The confusion matrix scores only the ~8% of records that reach the model. That is
  the design working, but it means the classifier's per-category numbers rest on small
  support — 4 to 16 cases per category on the standard set.
- Threshold previews run on bundled datasets only, because they need the original rows
  on disk. Uploaded runs return 422 rather than silently reconciling something else.
