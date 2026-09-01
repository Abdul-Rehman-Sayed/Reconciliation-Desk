# 5-minute run sheet

**What this app does, in one sentence:** a merchant's own records and their bank
statement are two lists of the same money that never quite line up, and this app
lines them up — matching what it can prove, flagging what it can't, and never
guessing.

Every number below is from the **adversarial** dataset, because that's the one this
run sheet tells you to demo. Verified against the engine on 2026-09-01.

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
   then reconcile + explain both datasets once. On the adversarial set that's 6
   requests out of 1,000/day, on top of what is already cached. Every rehearsal
   after that is free — served from `data/llm_cache.json`, and the status strip
   reads `cached 66/66`.
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
> engine was never tuned for — ambiguous decoys, partial settlements, references
> that only appear in the narration text, and settlements up to 45 days late.
> We're going to run the hard one."

Click **Adversarial** → **Reconcile the stress set**.

### 1:00 — Processing (45s) — *the architecture slide, except it's real*

Let the passes tick by. Talk over them.

> "Six passes of plain rules, in order. Exact matches first. Then reversals.
> Then tolerant — that one knows the gateway's fee formulas and the settlement
> windows. Then duplicates. Then composite, for when four payments settle as one
> credit. Then fuzzy reference matching. Each pass only sees what the one before it
> couldn't handle. All six run in about four hundredths of a second — the pacing
> here is for you, and the real timing per pass is printed next to each stage."

When the lines start drawing:

> "Every line is a match the engine actually committed. Both columns are sorted by
> date, so the interesting thing isn't the lines — it's **the rows with no line**."

Last stage:

> "*Now* the model runs. 66 groups of exceptions, 149 records out of 877 — 17% of
> the data. It never sees a clean match. It only gets what the rules couldn't settle."

### 1:45 — Summary (90s) — *the money moment*

> "83% resolved by rule alone. 728 of 877 records. No human, no model, no API call."
>
> "99% if you count what the model proposed — but be careful: those 141 records
> are **not** resolved. The engine found a likely answer and deliberately refused to
> commit it."
>
> "And 8 records where there is genuinely nothing on the other side. Those stay
> unresolved. That's the honest floor."

If they care about money rather than row counts:

> "By value that's 79% of 85 lakh — about 8.5 million rupees in the batch."

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

> "That marks the exception, timestamps it, and writes an audit row — including what
> the model suggested and whether the human agreed. Approve, reject, investigate.
> A person decides. There is no path in the code that moves money on its own."

If asked what Investigate does beyond that: it parks the item and signs your name to
that decision. Assignment and workflow aren't built — this is the decision-capture
layer, not a case management system. Say that plainly; don't imply a next step exists.

### 4:15 — Evidence (60s) — *the credibility screen, if you have the time*

From the summary, click **See the evidence**. This screen answers "how do you know?"
Every number on it is computed from data already on disk — opening it calls no model.

Lead with the baseline table:

> "This is what we're better than. The naive join — match on reference and amount,
> exactly, which is what most teams already have in a spreadsheet — gets 100%
> precision but only finds 292 of the 437 real pairs. It misses 145 of them."

Then the LLM-only baseline. **Read the caveat, it's on the screen anyway:**

> "And the opposite extreme: hand both sides to the model with no rules at all. We
> measured that on a fixed 40-record sample, not the full batch, and it's labelled as
> a projection everywhere it appears. On that sample it found every real pair *and
> invented one that doesn't exist*. 95% precision. In reconciliation the wrong match
> is the expensive one, because unlike a missed one, nobody ever notices it."

Then the confusion matrix:

> "Precision and recall broken out per flaw type, not one blended number that hides
> the bad cases. And notice how narrow it is: of the 400 cases, only 46 ever reach
> the model. 346 are settled by a rule, with a proof attached, before an LLM is asked
> anything. That gap is the design, not a hole in the measurement."

Then calibration — the one nobody else will have:

> "We also checked whether the confidence number means anything: stated confidence
> against measured accuracy, in bins. A confidence score nobody has verified is
> just decoration."

Point at hours saved:

> "11 hours of manual work becomes under 6 hours of reviewing a queue. Call it five
> hours back per batch, most of a working day. And we count the queue as real work
> instead of pretending review is free — 4 minutes an exception, 45 seconds a clean
> line, both assumptions printed on screen so you can argue with them. That's why
> this number is lower than the 83% match rate would suggest."

**The slider, if the room is engaged — hand it to them.**

Drag **Auto-resolve floor** from 0.85 up to 0.95.

> "That's you telling the engine to be more certain before it acts alone. The match
> rate drops from 83% to 64%, the queue grows from 86 exceptions to 171, and recall
> stays at 100% — nothing was lost, it just moved to a person. That took a fraction
> of a second, and it did not call the model. It *can't*: moving this slider only
> changes the rules layer, and there is no code path from it to Groq. It even tells
> you what re-explaining at the new setting would cost, before you could spend it."

Make that last point deliberately if an audience member is the one dragging. They
should not be able to spend your quota, and here they structurally cannot.

### 5:15 — Close (15s)

> "The rules engine handles 83–90% and costs nothing to run. The model handles the
> 10–17% that's genuinely ambiguous, and explains it in plain English. A person
> decides the rest. The number we'd defend is precision, not match rate."
