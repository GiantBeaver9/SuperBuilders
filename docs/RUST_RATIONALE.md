# Why the readiness-gap ordering lives in Anki's Rust engine

The PRD requires a *real modification to Anki's Rust scheduling/query engine, not
just a Python UI layer*. This documents that change: what it is, why it belongs
in Rust, and how it is tested (Rust unit tests + a Python integration test that
calls into the Rust function), with the undo/corruption argument.

## The change

A new `SchedulerService` RPC, **`ComputeReadinessGap`**, added to Anki's core
scheduling service:

- **Protobuf** (`proto/anki/scheduler.proto`): `ComputeReadinessGap(ComputeReadinessGapRequest) → ComputeReadinessGapResponse`, with `ConceptGap` (card ids + novel accuracy + exam weight per concept) and `ConceptStake` (the computed result).
- **Rust** (`rslib/src/scheduler/readiness.rs`): for each concept, it loads the
  supplied cards, reads each card's FSRS memory state, and computes
  `card_mastery` = the **mean retrievability** using Anki's own
  `fsrs::current_retrievability` — the exact function the retrievability stats
  graph uses — then `points = (card_mastery − novel_accuracy) × exam_weight`,
  and returns the concepts **ordered by points descending** (largest gap first).
- Wired into `impl SchedulerService for Collection` (`rslib/src/scheduler/service/mod.rs`);
  module registered in `rslib/src/scheduler/mod.rs`.

This is the native, in-engine version of the project's "points-at-stake queue"
(traceability row in `PREREGISTRATION.md`): it ranks concepts by *how much the
card score overstates real readiness*.

## Why it belongs in Rust, not the Python/aqt layer

1. **It reads FSRS memory state per card on a hot path.** The ordering is
   recomputed as the due set changes; on a 50k-card deck that is tens of
   thousands of card-state reads plus a retrievability evaluation each. Doing it
   in Rust keeps it in the same process and memory as the card store, with no
   per-card Python↔backend round-trip (each of which crosses the protobuf/FFI
   boundary).
2. **Consistency with the scheduler.** Retrievability is computed with
   `fsrs::current_retrievability` and `FSRS5_DEFAULT_DECAY` — the *same* code the
   scheduler and the stats graphs use. A Python re-implementation (which the
   add-on also has, for the sidecar path) can drift from Anki's FSRS version; the
   engine function cannot. The add-on's Python `gap.mastery` is pinned equal to
   the SQL `pow` path by a test, and this Rust path is the authoritative one.
3. **It is a query over collection-owned data.** Card memory state, decay, and
   last-review time live in the Rust `Card`; exposing a typed RPC is how Anki's
   architecture surfaces such queries to every client (desktop Python, and — via
   the same protobuf — a mobile/AnkiDroid client).

The novel-accuracy and exam-weight inputs come from the add-on's `gap.db`
sidecar (data Anki has no notion of) and are passed in the request, so the engine
change stays generic and the sidecar stays append-only and outside the
collection.

## Tests

**Rust unit tests** (`rslib/src/scheduler/readiness.rs`, `#[cfg(test)]`):

1. `points_is_gap_times_weight` — the points formula, including the negative-gap
   case (novel accuracy above card mastery) and zero exam weight.
2. `retrievability_is_one_at_zero_elapsed_and_decreases` — `card_retrievability`
   returns R = 1 at t = 0 and strictly decreases with elapsed time (via the real
   `fsrs::current_retrievability`).
3. `mean_mastery_and_points_compose` — mean-of-R aggregation composes correctly
   into the points key.
4. `orders_by_gap_and_handles_empty_concepts` — end-to-end through
   `Collection::compute_readiness_gap` on a real (empty) collection: concepts
   with no scored card get mastery 0.0 and are ordered by gap descending.

**Python integration test** (`rust-fork/test_readiness_integration.py`): builds a
real collection with the fork's `pylib`, creates cards, sets FSRS memory state,
and calls `col._backend.compute_readiness_gap(...)` — i.e. Python calling the new
Rust function across the protobuf bridge — asserting the returned mastery,
points, and ordering. See `rust-fork/README.md` for how to run it against the
built wheel.

## Undo and corruption

`ComputeReadinessGap` is **read-only**: it opens no transaction, writes no card,
note, or revlog, and returns a computed result. Therefore:

- It **cannot corrupt** the collection — there is no write path. (The Python
  integration test additionally asserts the collection's modification is
  unchanged and the undo status is untouched after the call.)
- It takes **no part in undo** — only mutating operations (Anki `Op`s) enter the
  undo queue; a pure query does not, by construction. Anki's existing
  transactional `Op`/`transact` machinery is what guarantees undo for mutations,
  and this change adds none.

## Applying the change to a clean Anki checkout

The change is captured as a patch and the changed files under `rust-fork/`
(against Anki 26.05). `rust-fork/README.md` has the apply + build + test steps
(`just test-rust` for the Rust tests; build the wheel for the Python test).
