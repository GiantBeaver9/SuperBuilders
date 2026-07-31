# Queries — one file per group, executed as needed

Each file is a self-contained group of SQL that runs at a specific point in the
collection's life. None of them is a monolith; run the one the moment calls for.
All obey [`CONVENTIONS.md`](CONVENTIONS.md) — the shared definitions of
exposure, mastery, and novel accuracy — so the groups agree with each other.

Every query runs with the Anki collection as the primary connection (`main`)
and the sidecar `gap.db` ATTACHed as `gap`. Tables are fully qualified; there
are no bind parameters, so each file runs as-is under `sqlite3` or the
validator.

## Execution order

| When | File | Reads | Writes |
|---|---|---|---|
| **Every backend open** | `01_open/rebuild_note_concepts.sql` | `main.notes`, `gap.concepts` | `gap.note_concepts` (rebuild) |
| **Once per concept, before first exposure** | `02_assign/assign_arms.sql` | `gap.concepts` | `gap.arms` (insert-or-ignore) |
| **During study, each queue build** | `03_queue/points_at_stake.sql` | `main.cards/revlog`, `gap.*` | — (read-only) |
| **Before endpoints (gate)** | `05_discipline/leakage_check.sql` | `main.*`, `gap.*` | — |
| **Analysis** | `04_endpoints/primary_crossover.sql` | `main.revlog`, `gap.*` | — |
| | `04_endpoints/latency_dissociation.sql` | `main.revlog`, `gap.novel_revlog` | — |
| | `04_endpoints/terminal_novel_accuracy.sql` | `gap.*` (held-out) | — |
| | `04_endpoints/throughput_cost.sql` | `main.revlog`, `gap.arms/retirements` | — |
| | `04_endpoints/arm_c_sanity.sql` | `gap.*` (held-out) | — |
| **Dashboard** | `05_discipline/abstain_rule.sql` | `gap.*` | — |

`01_open` and `02_assign` are the only writers, and both only touch `gap.*` —
never `main`. `02_assign` is insert-or-ignore, so an assigned concept is never
rewritten (assignment must predate first exposure). Everything under `04_` and
the checks in `05_` are strictly read-only `SELECT`s.

`leakage_check.sql` must be run and report clean *before* any endpoint is
computed — its summary statement emits a `violation_count` row per check, and
all-zero is the go-ahead.

## The primary endpoint

`primary_crossover.sql` emits two result sets: (a) the attempt-level table (one
row per practice novel attempt, with the concept's as-of exposure count, the
`1-4`/`5+` bucket, `correct`, latency, weight) that feeds the mixed-effects
logistic regression run *outside* SQL, and (b) a descriptive contrast whose
`diff_pp_gate_minus_nogate` column makes the committed sign flip — negative at
`1-4`, positive at `5+` — directly readable.

## Per-student vs. across-student

One collection == one student (no `user_id`). These queries operate on the one
student in the attached `gap.db`; the student × concept mixed model is built by
UNION-ing the per-student exports and tagging each row with its source file.

## Validating

```sh
python3 scripts/validate_sql.py            # every queries/**/*.sql, against empty tables
python3 scripts/validate_sql.py <file...>  # just these files
python3 scripts/e2e_seed_test.py           # end-to-end on seeded data (writes + reads + crossover)
```

`validate_sql.py` builds `schema/main_stub.sql` as `main`, attaches a fresh
`gap.db` from `schema/gap.sql`, and runs each file inside a rolled-back
transaction — empty tables still surface bad columns, table refs, and syntax.
`e2e_seed_test.py` seeds a small engineered scenario, runs the operational
writers, then exercises every analysis query on populated rows and prints the
crossover contrast.

## Design decisions resolved (schema v2)

The three tensions the first build surfaced are now settled in the schema. See
`CONVENTIONS.md` for the authoritative definitions.

- **Retirement is persisted, not derived.** `gap.retirements` (concept_id,
  `retired_ms`, `trigger`) is the app's ground-truth signal — a concept is
  retired iff it has a row. `throughput_cost.sql` counts those rows instead of
  reconstructing retirement from card/novel state. `vanilla` retirements are
  recorded by the sidecar *observing* unmodified Anki (`trigger='anki_default'`);
  it never writes `main.*`.
- **Baseline difficulty is a real per-concept column.** `gap.concepts.baseline_difficulty`
  is authored before first exposure, so `assign_arms.sql` stratifies on a genuine
  per-concept value; it falls back to the FSRS `D0(3)` proxy only where the
  column is NULL.
- **FSRS read location is verified.** `cards.data.$.s`/`$.d` are the exact serde
  keys Anki's Rust backend writes (`CardData` in `rslib/src/storage/card/data.rs`).
  Every mastery query still isolates the read in a `card_state` CTE so a
  non-standard backend is a one-line change per file.
