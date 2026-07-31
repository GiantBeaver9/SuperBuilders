# Query conventions — the shared contract

Every file under `queries/` obeys these. They exist so the analysis coheres:
one definition of *exposure*, one of *mastery*, one of *novel accuracy*, used
everywhere. If a definition changes, it changes here first, then in the files.

## Databases and aliasing

- The Anki collection is the primary connection (`main`). Its schema for
  validation is `schema/main_stub.sql`.
- The sidecar is `schema/gap.sql`, ATTACHed as **`gap`** at backend open.
- **Qualify every table**: `main.revlog`, `main.cards`, `main.notes`,
  `gap.arms`, `gap.novel_revlog`, … No unqualified table names — a query that
  reads both databases must make the source obvious.

## Hard rules (from the pre-registration)

- **Read-only on `main`.** No query writes `main.revlog`, `main.cards`,
  `main.notes`, or `main.col`. Writes land only in `gap.*`.
- **Append-only on `gap` measurement tables.** `gap.novel_revlog` and
  `gap.novel_items` are never UPDATEd by a query here. `gap.arms` is written
  once per concept, before first exposure, and never rewritten.
- **`is_holdout` is set at insert, never by a query.** No file here assigns it.
- **No bind parameters** (`?`, `:name`) in these `.sql` files — they must run
  as-is under `scripts/validate_sql.py` and `sqlite3`. Where a query needs
  "now", use `(unixepoch('subsec') * 1000)` for epoch-ms, matching Anki's ids.

## Concept membership

A card belongs to a concept via its note's guid:

```
main.cards c
  JOIN main.notes n        ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
  -- nc.concept_id is the concept
```

`gap.note_concepts` is a derived index rebuilt from `main.notes.tags`
(tag form `concept::<code>`); it is never the source of truth.

## Exposure count — the crossover's x-axis

An **exposure** is one review of one of a concept's cards. The exposure count
for a concept, as of timestamp `t` (epoch ms), is:

```
COUNT(main.revlog r)  where r.cid is a card of the concept  and r.id <= t
```

**Exposure bucket** (committed in the pre-registration):

```
CASE WHEN <exposure_count> <= 4 THEN '1-4' ELSE '5+' END
```

A **novel attempt** is bucketed by the concept's exposure count *at the moment
of that novel attempt* — i.e. count the concept's `main.revlog` rows with
`r.id <= novel_revlog.id`. This is the join that lets novel accuracy be read as
a function of accumulated card exposure. It is the mechanism the primary
endpoint tests.

## Card mastery — FSRS retrievability

Mastery is **not** binary correct. Per the pre-registration it is FSRS
retrievability, `revlog.ease` (1–4), and `revlog.time`.

FSRS-5 retrievability of a single card, elapsed `t` days since its last review,
stability `S`:

```
R(t) = (1 + FACTOR * t / S) ^ DECAY
       DECAY  = -0.5
       FACTOR = 19.0/81.0        -- = 0.9^(1/DECAY) - 1
```

Read state out of `main.cards.data` (JSON):

```
json_extract(c.data, '$.s')   -- stability  S
json_extract(c.data, '$.d')   -- difficulty D
```

Elapsed days `t` = `(now_ms - last_review_ms) / 86400000.0`, where
`last_review_ms = MAX(main.revlog.id)` over the card's reviews.

> **ASSUMPTION — single point to adjust.** FSRS memory state is read from
> `cards.data.$.s`/`$.d`. Anki's storage of FSRS state has drifted across
> versions; if the target collection stores it elsewhere, change the two
> `json_extract` calls (and only those). Every file that computes mastery
> isolates this in one CTE named `card_state` so the edit is one place per file.

**`card_mastery(concept)`** = mean `R` across the concept's cards that have a
memory state (cards with no reviews yet contribute no `R`).

## Novel accuracy

`gap.novel_revlog.correct` (0/1), joined to a concept through
`gap.novel_item_concepts`:

```
gap.novel_revlog nr
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  JOIN gap.novel_items ni          ON ni.id = nr.item_id
```

- **Practice novel accuracy** (drives the retirement gate ≥ 0.7 and the queue):
  `AVG(nr.correct)` over items with `ni.is_holdout = 0`.
- **Terminal / held-out novel accuracy** (the terminal endpoint): the same,
  over `ni.is_holdout = 1`. The held-out set is never used for gating or
  ordering — only for the terminal contrast.

Keep the two strictly separate. A query that gates or orders must filter
`is_holdout = 0`; the terminal endpoint filters `is_holdout = 1`.

## Arms

`gap.arms.arm ∈ {'gate','nogate','vanilla'}` = Arm A / B / C. One row per
concept, `assigned_ms` predating the concept's first exposure. Contrasts are
reported as **A − B** (`gate` − `nogate`); `vanilla` is the sanity check.

## Baseline difficulty for stratification

The pre-registration stratifies arm assignment by exam weight and by *baseline*
FSRS initial difficulty, binned into terciles. Arms are assigned **before first
exposure**, when a card's realized FSRS difficulty does not yet exist. The
baseline used here is the FSRS **default initial difficulty** `D0(G)` evaluated
at the "Good" first grade (G = 3) from the deck's FSRS weights — the
pre-exposure expectation, not a realized value.

> **ASSUMPTION — surfaced for confirmation.** Using `D0(3)` as the pre-exposure
> difficulty proxy is a design choice, not a value read from the collection.
> The assign query isolates it in one CTE named `baseline_difficulty` and
> documents the `D0` weights it uses. If a different baseline is intended,
> change that CTE only.

## Output shape

Analysis files `SELECT` a tidy result set (one row per unit, named columns) and
change nothing. Operational files (`01_open`, `02_assign`) write only to `gap.*`
and are safe to re-run (idempotent where stated). Each file starts with a
header comment: what it is, when it runs, what it reads, what it writes.
