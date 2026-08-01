-- assign_arms.sql — LIFECYCLE SETUP
--
-- WHAT:  Stratified randomized split of this collection's concepts into the
--        three arms {'gate','nogate','vanilla'} (Arm A / B / C). Stratified by
--        exam-weight tercile and baseline-difficulty tercile; within each
--        stratum the concepts are ordered by a STABLE deterministic hash of the
--        concept code (not random()) and assigned round-robin. Reproducible by
--        construction — the pre-registration requires a fixed, replayable split.
-- WHEN:  Once per concept, BEFORE that concept's first exposure. Safe to re-run:
--        already-assigned concepts are never touched.
-- READS: gap.concepts (id, code, weight) and gap.arms (to skip assigned ones).
-- WRITES: gap.arms only (INSERT OR IGNORE). Read-only on main. Assignment
--         predates first exposure and is never rewritten (append-only on arms).
--
-- NOTE ON STRATA POPULATION: terciles are computed over the FULL concept pool of
-- this collection, not just the unassigned subset, so the stratum boundaries and
-- the resulting arm for each concept are identical on every run. Only concepts
-- absent from gap.arms are inserted; INSERT OR IGNORE additionally guards the
-- primary key so an already-assigned concept is never overwritten.

WITH
-- Baseline (pre-exposure) FSRS difficulty for stratification. Preferred source
-- is the persisted per-concept `concepts.baseline_difficulty`, authored before
-- first exposure (schema v2). When it is NULL we fall back to the FSRS default
-- INITIAL difficulty D0(G) at the "Good" first grade G = 3:
--
--     D0(G) = w4 - exp(w5 * (G - 1)) + 1,  clamped to [1, 10]
--
-- with the FSRS-5 default weights  w4 = 7.1949,  w5 = 0.5345, giving
--     D0(3) = 7.1949 - exp(1.069) + 1 = 5.282434  (within [1,10], clamp inert).
--
-- This runs LIVE inside Anki, whose bundled SQLite ships NO math functions
-- (no exp/pow/sqrt) — so the fallback is the PRECOMPUTED constant 5.282434,
-- not an exp() call. Since it is one scalar for every NULL-difficulty concept,
-- baking the value in changes nothing but portability. (See CONVENTIONS.md
-- "Math functions" and "Baseline difficulty".)
--
-- NOTE: once every concept carries a real baseline_difficulty the difficulty
-- tercile is a genuine stratifier. Concepts left NULL share the constant and so
-- land in one undiscriminated band — populate the column to stratify. To change
-- the fallback, edit ONLY this literal.
baseline_difficulty AS (
  SELECT
    c.id     AS concept_id,
    c.code   AS code,
    c.weight AS weight,
    COALESCE(c.baseline_difficulty, 5.282434) AS d0
  FROM gap.concepts c
),
-- Bin the full pool into exam-weight and difficulty terciles. ntile ORDER BY is
-- tie-broken by code so the binning is deterministic even when values tie (the
-- difficulty column is constant under the D0(3) proxy, so its tercile is
-- effectively a stable arbitrary partition — documented, not accidental).
strata AS (
  SELECT
    concept_id,
    code,
    weight,
    d0,
    ntile(3) OVER (ORDER BY weight, code) AS weight_tercile,
    ntile(3) OVER (ORDER BY d0, code)     AS difficulty_tercile
  FROM baseline_difficulty
),
-- Stable deterministic hash of the concept code with a fixed documented salt.
-- Polynomial rolling hash, base 131, modulus 1000000007 (both bound every
-- intermediate below 2^63 so no SQLite integer→float overflow ever occurs, which
-- keeps the result identical across platforms). Fixed salt = 2166136261 seeds
-- the fold; changing the salt reshuffles the within-stratum order and is the one
-- knob that alters the (reproducible) assignment.
code_hash(concept_id, code, i, h) AS (
  SELECT concept_id, code, 1, 2166136261 % 1000000007
  FROM strata
  UNION ALL
  SELECT
    concept_id,
    code,
    i + 1,
    (h * 131 + unicode(substr(code, i, 1))) % 1000000007
  FROM code_hash
  WHERE i <= length(code)
),
hashed AS (
  SELECT concept_id, h AS hash
  FROM code_hash
  WHERE i = length(code) + 1
),
-- Order concepts within each (weight, difficulty) stratum by the stable hash
-- (tie-broken by code), then assign arms round-robin: 0->gate, 1->nogate,
-- 2->vanilla.
ordered AS (
  SELECT
    s.concept_id,
    row_number() OVER (
      PARTITION BY s.weight_tercile, s.difficulty_tercile
      ORDER BY h.hash, s.code
    ) - 1 AS pos
  FROM strata s
  JOIN hashed h ON h.concept_id = s.concept_id
),
assignment AS (
  SELECT
    concept_id,
    CASE pos % 3
      WHEN 0 THEN 'gate'
      WHEN 1 THEN 'nogate'
      ELSE 'vanilla'
    END AS arm
  FROM ordered
)
INSERT OR IGNORE INTO gap.arms (concept_id, arm, assigned_ms)
SELECT
  a.concept_id,
  a.arm,
  (unixepoch('subsec') * 1000)
FROM assignment a
WHERE a.concept_id NOT IN (SELECT concept_id FROM gap.arms);
