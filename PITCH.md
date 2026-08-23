# 5-minute run sheet

Dry-run timing: the whole demo path takes **21 seconds** of clicking. Everything
else is talking. Run the backend and frontend first, and open the start screen
before you begin.

### Before you present — 60 seconds of setup

1. **Warm the cache with a real run.** `USE_MOCK_LLM=false` in `backend/.env`, then
   reconcile + explain both datasets once. Costs ~7 requests out of 1,000/day. After
   that every rehearsal is served from `data/llm_cache.json` at zero cost, and the
   status strip shows `cached 32/32`.
2. **Check what the demo will cost before you run it:**
   `curl -X POST 'localhost:8000/api/runs/{id}/explain?dry_run=true'` — you want
   `"would_call_for": 0`.
3. **Leave `USE_MOCK_LLM=false` for the pitch.** The stand-in is for building; every
   verdict it produces is stamped as a stand-in in the UI, and you do not want to be
   explaining that on stage.
4. `python scripts/check_limits.py` if you want the current headroom in your pocket.

---

### 0:00 — The problem (30s, no slides)

> "A merchant's ledger and their bank statement are two records of the same money,
> and they never agree. Settlement lands three days late. The gateway takes 2.36%
> before the money arrives. A webhook fires twice. Four payments settle as one
> credit whose reference matches none of them. Someone does this by hand every
> month."

### 0:30 — Start screen (30s)

Point at the two dataset cards.

> "400 ground-truth cases, injected with nine flaw categories at known
> proportions. The second set keeps all nine and adds four the engine was never
> tuned for. We're going to run the hard one."

Click **Adversarial** → **Reconcile the stress set**.

### 1:00 — Processing (45s) — *the architecture slide, except it's real*

Let the passes tick. Talk over it.

> "Six deterministic passes, in order. Exact. Reversals. Tolerant — that one knows
> the gateway fee formulas and the settlement windows. Duplicates. Composite, for
> N-rows-settle-as-one. Fuzzy reference. Each one only sees what the last one
> couldn't do. The whole thing takes 32 milliseconds — the pacing is for you, and
> the real per-pass timing is printed next to each stage."

When the lines are drawing:

> "Every line is a match the engine actually committed. Both columns are
> date-sorted, so what you want to look at is **the rows with no line**."

Last stage:

> "*Now* the model runs. 66 of 877 records — under 8%. It never saw a clean match."

### 1:45 — Summary (90s) — *the money moment*

> "83% resolved by rule. No human, no model, no API call."
> "99% including what the model proposed — but those 141 records are **not**
> resolved. The engine found a candidate and refused to commit it."
> "And 8 records where there is genuinely nothing on the other side. Those stay
> unresolved. They're the honest floor."

Scroll to the accuracy panel. **Read the caveat out loud before the numbers** — it
is the whole credibility play:

> "This section only exists because we generated the data and held the answer key
> back. Real reconciliation has no answer key. On your own files this app shows no
> accuracy score at all."

Then:

> "100% precision. Zero wrong matches among the 354 the engine committed on its
> own. Cross-checked on 9 unseen seeds per dataset — auto-resolve precision never
> dropped below 100% on any of the 18 runs."

**If they push on the 100%** — they should, and this is the answer you want:

> "You're right to. So look at the two datasets side by side. Precision holds at
> 100% on both. What moves is the auto-resolve rate: 90% down to 83%. Harder data
> doesn't make it wrong, it makes it ask for more help. That's the only failure
> mode I'd accept in front of a ledger."

### 3:15 — Exceptions (90s) — *the human gate*

Click **Review 86 exceptions**. Filter to **Below 0.50**.

> "Sorted hardest first. This one" — open a contested decoy —

> "the bank truncated the reference so far that it fits two ledger rows equally
> well. Only the amount separates them. The engine picked one, capped its own
> confidence at 0.45, and said 'worth a second pair of eyes.' It could have just
> matched it silently. That's the bug we'd rather have."

Open an orphan.

> "Ledger says captured. Bank never saw it. Six passes, nothing. It shows you the
> nearest candidate and tells you the engine *rejected* it. It does not invent a
> match."

Click **Investigate**. Point at the state change and the counter.

> "That's the only thing that moves in this system. Approve, reject, investigate.
> There is no auto-execute path in the codebase."

### 4:15 — Evidence (60s) — *the credibility screen, if you have the time*

From the summary, click **See the evidence**. This screen is the answer to "how do
you know", and every number on it is computed from data already on disk — opening it
calls no model.

Lead with the baseline table:

> "Here's what we're actually better than. The naive join — one equality match on
> reference and amount, what most teams already have in a spreadsheet — gets 100%
> precision and misses 100 real pairings. And here's the version where the model
> decides everything: it found every real pair *and invented one that doesn't
> exist*. 95% precision. In reconciliation the wrong match is the expensive one,
> because unlike a missed one it's silent."

Then the confusion matrix:

> "Precision and recall per flaw category, not one blended number. And notice how
> narrow it is — 120 of the 400 cases never reach the model at all. A gateway fee
> and a settlement delay are resolved by a rule, with a proof, before anything is
> asked of an LLM. That gap is the architecture, not a hole in the testing."

Then calibration, which is the one nobody else will have:

> "And we checked whether the confidence number means anything. Stated confidence
> against measured accuracy, binned. A confidence score nobody has verified is
> decoration."

Point at hours saved:

> "10.7 hours of manual work becomes 3.5 hours of queue. About one working day per
> batch. We count the queue as real work rather than pretending it's free — that's
> why the number is lower than the match rate would suggest."

**The slider, if the room is engaged — hand it to them.**

Drag **Auto-resolve floor** from 0.85 up to 0.95.

> "That's you demanding more certainty before the engine commits alone. Match rate
> drops 90% to 72%, and recall stays at 100% — nothing was lost, it moved into the
> queue for a person. Twenty-five milliseconds, and it did not call the model. It
> *can't*: threshold changes only move the deterministic layer, and there's no code
> path from that slider to Groq. It tells you what explaining the new setting would
> cost before you could spend it."

That last point is the one to make deliberately if an audience member is the one
dragging. An audience member should not be able to spend your quota, and here they
structurally cannot.

### 5:15 — Close (15s)

> "Deterministic engine does 83–90% and costs nothing to run. The model handles
> the 8% that's genuinely ambiguous and explains it in English. A person decides
> everything else. The number we'd defend is precision, not match rate."

---

## Questions you should expect

**"Did you tune the thresholds to your own answer key?"**
Partly — that's why the cross-seed holdout is in the README. 9 unseen seeds per
profile, and an adversarial set built specifically to break assumptions. Its first
run scored 0/12 on ambiguous decoys and 0/12 on late settlements, which is how two
real bugs got found.

**"Why is the LLM barely used?"**
Because a rule is cheaper, faster, auditable and deterministic. Every time we caught
ourselves routing something to the model that a rule could do, we wrote the rule —
narration reference-mining is the clearest example. Under 8% of records reach it.

**"What happens if Groq is down?"**
The engine is unaffected; it needs no key. Exceptions come back marked `unavailable`
with the reason, and the engine's own finding still stands. Nothing is fabricated.
You can demo this by removing the key.

**"What would break on real data?"**
Composite matching depends on mining the counterparty from the narration — a batch
whose narration names no recognisable party is missed. Batches over 4 rows aren't
searched. The date windows are calibrated for Indian PG settlement. All in the
README under Known limits.

**"Does this work with Razorpay?"**
There's a column adapter for the settlement recon report — drop one on the upload
screen and it's detected by its columns. The detail worth knowing: you cannot join on
`settlement_utr`. Razorpay assigns a UTR to the settlement it initiates, but the
correspondent bank that actually credits the merchant issues its own reference, and
that's what shows up in the statement narration. Different strings, same money. The
reliable shape is two-level — bank credit to settlement batch on amount and date,
then transaction to batch on `settlement_id`, identified by `order_id`. The adapter
does the mapping and exposes the batch grouping; the two-level matcher itself is
listed as future work rather than half-built.

**"How much did the demo you just ran cost?"**
Zero. It was served from cache. A cold run is 3 requests for 32 exceptions — we batch
12 per call. The whole redesign came out of a real failure: the first version sent 5
per call, 3 in parallel, with `max_tokens=2000`, and got 429s on 4 of 7 calls. Groq
reserves `max_tokens` against the per-minute token bucket at admission, so three
concurrent calls were reserving 9,180 tokens against a bucket of 8,000. The
completion length was never the problem — the reservation was.

**"Isn't 100% precision suspicious?"**
Yes, and you should push on it. Three answers. The cross-seed holdout: 9 unseen seeds
per profile, auto-resolve precision never below 100% across 18 runs. The adversarial
set, whose first run scored 0/12 twice and found two real bugs. And the slider — tighten
the same-day window to zero on stage and precision drops to 99.07%, which tells you
the measurement is live rather than a constant we typed in.
