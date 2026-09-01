# 5-minute run sheet

**What this app does, in one sentence:** a merchant's own records and their bank
statement are two lists of the same money that never quite line up, and this app
lines them up — matching what it can prove, flagging what it can't, and never
guessing.

Dry-run timing: the whole demo path takes **21 seconds** of clicking. Everything
else is talking. Run the backend and frontend first, and open the start screen
before you begin.

**Words you'll say, defined once so you can use them freely:**

- **Ledger** — the merchant's own record of payments. **Statement** — what the
  bank actually shows. The job is to pair them up.
- **Match** — the engine says these two rows are the same money.
- **Exception** — the engine could not pair a row confidently, so a person looks at it.
- **Orphan** — a row with nothing on the other side at all.
- **Precision** — of the matches the engine made on its own, how many were right.
  This is the number that matters: a wrong match is silent and expensive.
- **Recall** — of the real pairs that exist, how many the engine found. A miss is
  cheap by comparison — it just lands in someone's queue.

---

### Before you present — 60 seconds of setup

1. **Warm the cache with a real run.** Set `USE_MOCK_LLM=false` in `backend/.env`,
   then reconcile + explain both datasets once. That costs about 3 requests out of
   1,000/day on top of what is already cached. Every rehearsal after that is free —
   it is served from `data/llm_cache.json`, and the status strip reads `cached 66/66`.
2. **Check what the demo will cost before you run it:**
   `curl -X POST 'localhost:8000/api/runs/{id}/explain?dry_run=true'` — you want
   `"would_call_for": 0`.
3. **Leave `USE_MOCK_LLM=false` for the pitch.** The stand-in model is for building.
   Every verdict it produces is labelled as a stand-in in the UI, and you do not want
   to be explaining that on stage.
4. Run `python scripts/check_limits.py` if you want the current headroom in your pocket.

---

### 0:00 — The problem (30s, no slides)

> "A merchant's ledger and their bank statement are two records of the same money,
> and they never agree. The money settles three days late. The gateway takes 2.36%
> before it arrives. A webhook fires twice, so one payment shows up as two. Four
> payments land as a single credit, with a reference that matches none of them.
> Today, someone sits down and does this by hand, every month."

### 0:30 — Start screen (30s)

Point at the two dataset cards.

> "400 cases where we already know the right answer, seeded with nine kinds of
> flaw in known proportions. The second set keeps all nine and adds four more the
> engine was never tuned for. We're going to run the hard one."

Click **Adversarial** → **Reconcile the stress set**.

### 1:00 — Processing (45s) — *the architecture slide, except it's real*

Let the passes tick by. Talk over them.

> "Six passes of plain rules, in order. Exact matches first. Then reversals.
> Then tolerant — that one knows the gateway's fee formulas and the settlement
> windows. Then duplicates. Then composite, for when four payments settle as one
> credit. Then fuzzy reference matching. Each pass only sees what the one before it
> couldn't handle. All six take 32 milliseconds — the pacing here is for you, and
> the real timing per pass is printed next to each stage."

When the lines start drawing:

> "Every line is a match the engine actually committed. Both columns are sorted by
> date, so the interesting thing isn't the lines — it's **the rows with no line**."

Last stage:

> "*Now* the model runs. 66 groups of exceptions, 149 records out of 877 — 17% of
> the data. It never sees a clean match. It only gets what the rules couldn't settle."

### 1:45 — Summary (90s) — *the money moment*

> "83% resolved by rule alone. No human, no model, no API call."
>
> "99% if you count what the model proposed — but be careful: those 141 records
> are **not** resolved. The engine found a likely answer and deliberately refused to
> commit it."
>
> "And 8 records where there is genuinely nothing on the other side. Those stay
> unresolved. That's the honest floor."

Scroll to the accuracy panel. **Read the caveat out loud before the numbers** — it
is the whole credibility play:

> "This panel only exists because we made the data and kept the answer key hidden
> from the engine. Real reconciliation has no answer key. Upload your own files and
> this app shows no accuracy score at all, because it would have nothing to check
> against."

Then:

> "100% precision. Of the 354 matches the engine committed on its own, none were
> wrong. And we checked that on 9 fresh unseen datasets per profile — precision
> never dropped below 100% on any of the 18 runs."

**If they push on the 100%** — they should, and this is the answer you want:

> "You should push on that. So compare the two datasets. Precision stays at 100%
> on both. What changes is how much it handles alone: 90% drops to 83%. Harder data
> doesn't make it wrong — it makes it ask for help more often. That's the only way
> I'd want this thing to fail in front of a ledger."

### 3:15 — Exceptions (90s) — *the human gate*

Click **Review 86 exceptions**. Filter to **Below 0.50**.

> "Hardest first." — open a contested decoy —
>
> "Here the bank cut the reference so short that it fits two ledger rows equally
> well. Only the amount tells them apart. The engine picked one, capped its own
> confidence at 0.45, and asked for a second pair of eyes. It could have quietly
> matched it and nobody would have known. That's the behaviour we want."

Open an orphan.

> "The ledger says this payment was captured. The bank never saw it. Six passes,
> nothing. So it shows you the closest thing it found and tells you it *rejected*
> it. It does not invent a match to look complete."

Click **Investigate**. Point at the state change and the counter.

> "That's the only thing that moves in this system: approve, reject, investigate.
> A person decides. There is no path in the code that moves money on its own."

### 4:15 — Evidence (60s) — *the credibility screen, if you have the time*

From the summary, click **See the evidence**. This screen answers "how do you know?"
Every number on it is computed from data already on disk — opening it calls no model.

Lead with the baseline table:

> "This is what we're better than. The naive join — match on reference and amount,
> exactly, which is what most teams already have in a spreadsheet — gets 100%
> precision but misses 100 real pairs. Now the opposite: let the model decide
> everything. It found every real pair *and invented one that doesn't exist*. 95%
> precision. In reconciliation, the wrong match is the expensive one, because unlike
> a missed one, nobody ever notices it."

Then the confusion matrix:

> "Precision and recall broken out per flaw type, not one blended number that hides
> the bad cases. And look how narrow it is: 120 of the 400 cases never reach the
> model at all. A gateway fee or a late settlement is handled by a rule, with a
> proof attached, before an LLM is asked anything. That gap is the design, not a
> hole in the measurement."

Then calibration — the one nobody else will have:

> "We also checked whether the confidence number means anything: stated confidence
> against measured accuracy, in bins. A confidence score nobody has verified is
> just decoration."

Point at hours saved:

> "10.7 hours of manual work becomes 3.5 hours of reviewing a queue. Roughly one
> working day back per batch. And we count the queue as real work instead of
> pretending review is free — that's why this number is lower than the match rate
> would suggest."

**The slider, if the room is engaged — hand it to them.**

Drag **Auto-resolve floor** from 0.85 up to 0.95.

> "That's you telling the engine to be more certain before it acts alone. The match
> rate drops from 83% to 64%, and recall stays at 100% — nothing was lost, it just
> moved into the queue for a person. That took a fraction of a second, and it did
> not call the model. It *can't*: moving this slider only changes the rules layer,
> and there is no code path from it to Groq. It even tells you what re-explaining at
> the new setting would cost, before you could spend it."

Make that last point deliberately if an audience member is the one dragging. They
should not be able to spend your quota, and here they structurally cannot.

### 5:15 — Close (15s)

> "The rules engine handles 83–90% and costs nothing to run. The model handles the
> 10–17% that's genuinely ambiguous, and explains it in plain English. A person
> decides the rest. The number we'd defend is precision, not match rate."

---

## Questions you should expect

**"Did you tune the thresholds against your own answer key?"**
Partly — which is exactly why the cross-seed holdout is in the README. 9 unseen
datasets per profile, plus an adversarial set built specifically to break our
assumptions. Its first run scored 0 out of 12 on ambiguous decoys and 0 out of 12
on late settlements. That's how we found two real bugs.

**"Why is the LLM barely used?"**
Because a rule is cheaper, faster, auditable, and gives the same answer every time.
Every time we caught ourselves sending something to the model that a rule could
handle, we wrote the rule — mining references out of the narration text is the
clearest example. Under 10% of records reach the model on the standard set, 17% on
the adversarial one.

**"What happens if Groq is down?"**
The engine doesn't care — it needs no key to run. Exceptions come back marked
`unavailable` with the reason, and whatever the engine itself found still stands.
Nothing gets fabricated to fill the gap. You can demo this by deleting the key.

**"What would break on real data?"**
Composite matching relies on finding the counterparty name in the narration text —
a batch whose narration names nobody recognisable gets missed. Batches larger than
4 rows aren't searched. The date windows are calibrated for Indian payment-gateway
settlement. All of this is in the README under Known limits.

**"Does this work with Razorpay?"**
There's a column adapter for the settlement recon report — drop one on the upload
screen and it's detected by its columns. The detail worth knowing: you cannot join
on `settlement_utr`. Razorpay assigns a UTR to the settlement it starts, but the
correspondent bank that actually pays the merchant issues its own reference, and
that's the one that shows up in the statement. Different strings, same money. The
shape that actually works is two levels — bank credit to settlement batch on amount
and date, then transaction to batch on `settlement_id`, identified by `order_id`.
The adapter does that mapping and exposes the batch grouping; the two-level matcher
itself is listed as future work rather than shipped half-built.

**"How much did the demo you just ran cost?"**
Zero — it came from cache. A cold run is 3 requests for 32 exceptions, because we
batch 12 per call. That design came out of a real failure: the first version sent 5
per call, 3 calls in parallel, with `max_tokens=2000`, and got rate-limited on 4 of
7 calls. Groq counts `max_tokens` against your per-minute budget the moment a call
is admitted, so three parallel calls were reserving 9,180 tokens against a bucket of
8,000. The replies were never that long — the *reservation* was the problem.

**"Isn't 100% precision suspicious?"**
Yes, and you should push on it. Three answers. One: the cross-seed holdout — 9
unseen datasets per profile, and precision never dropped below 100% across 18 runs.
Two: the adversarial set, which scored 0 out of 12 twice on its first run and found
two real bugs. Three: the slider — tighten the same-day window to zero on stage and
precision falls off 100% (to 99.77% on the adversarial set, 99.07% on the standard
one). That tells you the number is being measured live, not typed in as a constant.
