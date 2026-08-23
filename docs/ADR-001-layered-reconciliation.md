# ADR-001: A layered reconciliation engine, not rules, ML, or an LLM alone

**Status:** accepted · **Date:** 2026-08-22 · **Scope:** how the matching engine is built

## Context

Reconciliation compares two records of the same money — a merchant ledger and a
bank statement — that never quite agree. The disagreements are not random. They
fall into a small number of recurring shapes: a gateway fee deducted before
settlement, a settlement that landed three days late, a webhook that fired
twice, four payments settled as one credit, a reference number with a character
missing, and a genuine orphan with nothing behind it.

The failure that matters is asymmetric. A **missed** match costs an analyst a
few minutes of looking. A **wrong** match is silent: the books balance, the
exception queue is empty, and nobody finds out until an auditor does. Any design
here has to be judged on precision first, and specifically on the precision of
whatever it decides without asking a human.

Four architectures were available.

## Options

### 1. Pure deterministic rules

Write the join conditions out and run them.

- **For:** free, instant, fully explainable, perfectly reproducible, testable to
  the paisa. The 428 pairings in the standard dataset resolve in ~30ms.
- **Against:** rules only cover what was anticipated. A reference number damaged
  in a way nobody wrote a rule for is simply unmatched, and the system has
  nothing to say about it beyond "no match". Every new failure mode is a code
  change.
- **Measured:** the naive version of this — one equality join on reference and
  amount, the thing most finance teams already have in a spreadsheet — reaches
  **100% precision and 76.6% recall**. It gets every clean row and misses every
  fee deduction, every delay, and every damaged reference.

### 2. Pure ML anomaly detection

Train a model on historical matched pairs and score candidates.

- **For:** adapts to patterns nobody wrote down; handles drift without a code
  change.
- **Against:** three disqualifying problems here. There is no labelled history
  to train on — a new merchant has zero matched pairs, which is exactly when the
  tool is most needed. The output is a score with no argument attached, and an
  auditor asking "why is this matched" cannot be answered with a number. And an
  anomaly detector is built to find outliers, whereas most of this problem is
  finding *correspondences*, which is a different task wearing similar clothes.
- **Not built.** The cost is a training set that does not exist.

### 3. Pure LLM

Hand both sides to a language model and take its pairings.

- **For:** needs no rules, no training data, and no schema work. Handles the long
  tail on day one, and explains itself in plain language for free.
- **Against:** cost scales with every row rather than with the hard rows, latency
  goes from milliseconds to a minute, the same input can produce different
  answers on different days, and — the disqualifying one — it will confidently
  invent a pairing when none exists.
- **Measured, on a frozen 40-record subsample:** **95% precision, 100% recall**.
  It found every real pair and also proposed one that does not exist. Projected
  over the full 856 records: ~46,600 tokens against ~6,900 for the layered
  design, roughly **7x the tokens**, ~52 seconds against 30 milliseconds — and
  one silent wrong match per twenty.

### 4. Layered: deterministic first, LLM only on the remainder — **chosen**

Six deterministic passes run in order, each working only on what the previous
ones left behind. Passes 1–3 commit matches on their own. Passes 4–6 produce
*candidates* and never commit. Only what survives all six reaches a language
model, which classifies and explains but is structurally unable to resolve
anything — its output is a suggestion in a queue with a human gate in front of
it.

- **For:** each layer does what it is actually good at. Rules give proofs and
  cost nothing on the ~90% of records that are unambiguous. The model gives
  language and judgement on the ~8% that need it. The human decides on anything
  that could be wrong.
- **Against:** more moving parts than any single approach, two failure modes to
  reason about instead of one, and a boundary between the layers that has to be
  defended on purpose rather than drifting.
- **Measured:** **100% precision, 100% recall** on the standard dataset, with
  **90.2% of records resolved before a model was involved at all** and 100%
  precision on the subset the engine committed alone.

## Decision

Build the layered engine. The deterministic layer is the product; the LLM is a
feature of the exception queue.

The rule that keeps the layers honest: **the model never resolves anything.** It
has no code path to a match. It classifies, explains, and suggests — and a
person clicks. This is not a safety disclaimer bolted on afterwards, it is the
reason the precision number can be trusted, because the component capable of
inventing a plausible answer is not connected to anything that commits one.

## Consequences

**What this bought.** 90.2% of records never reach a model, so cost scales with
the hard rows rather than with volume. A cold run is 3 requests; a repeat run is
0, because verdicts are cached by a hash of the evidence. Every auto-resolved
match carries the pass, the rule, and the numbers that satisfied it, so
provenance is a lookup rather than a reconstruction.

**What it costs.** The exception queue is real work — 52 items on 856 records —
and the hours-saved figure counts it rather than pretending the queue is free.
Two layers means two places a bug can hide. And the boundary needs defending:
every rule added to the deterministic layer is one the model no longer sees, so
the split has to be re-measured rather than assumed.

**What would change this decision.** If a labelled corpus of matched pairs
appeared, option 2 becomes worth measuring as a *ranking* layer between passes 5
and 6 — scoring candidates the rules already found, never proposing new ones. If
frontier-model cost fell far enough that option 3 were free, it would still lose
on the 95% precision figure; cost is not the main argument against it, silence
is.

**A rule we removed for the same reason.** Composite matching originally had a
fallback that brute-forced subset sums on amount alone, with no evidence the
rows belonged together. Across ~1,450 candidate subsets per bank row it reliably
finds a coincidence within tolerance, and every false positive the engine
produced on both datasets came from that one rule. It is still in the file,
switched off, with the measurement written next to it — because "we tried this
and it cost precision" is worth more than a rule that quietly deleted itself.
