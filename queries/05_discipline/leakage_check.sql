-- leakage_check.sql — the §8 leakage gate.
--
-- WHAT: Data-discipline checks that must all report CLEAN before any endpoint
--   (primary crossover, terminal novel accuracy, secondaries) is computed. Each
--   check is a SELECT that returns OFFENDING rows only, tagged with a constant
--   `check_name`. The checks are UNIONed, so the first result set is the full
--   list of violations across all checks. An all-empty first result == clean.
--   A final summary SELECT emits one row per check_name with its violation
--   count (0 == clean), so a caller can assert cleanliness programmatically.
-- WHEN: Run after gap.* is populated / rebuilt and BEFORE computing endpoints.
-- READS: gap.novel_items, gap.novel_revlog, gap.novel_item_concepts, gap.arms,
--   gap.concepts, gap.note_concepts, main.revlog, main.cards, main.notes.
-- WRITES: nothing. Read-only. No bind parameters.
--
-- Union column contract (every check emits these three, same order):
--   check_name  TEXT     — constant identifying the check
--   offending_id         — the offending key (novel_item id / source_id / concept id)
--   detail      TEXT     — human-readable context for the violation

-- ---------------------------------------------------------------------------
-- Statement 1: the offending rows. Empty == clean.
-- ---------------------------------------------------------------------------
WITH
-- Concept's first card exposure (epoch-ms id of its earliest main.revlog row).
concept_exposure AS (
  SELECT nc.concept_id AS concept_id,
         MIN(r.id)     AS first_exposure
  FROM main.revlog r
  JOIN main.cards c        ON c.id = r.cid
  JOIN main.notes n        ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
  GROUP BY nc.concept_id
),
-- Each novel item's earliest novel attempt (epoch-ms id).
item_first_novel AS (
  SELECT nr.item_id     AS item_id,
         MIN(nr.id)     AS first_attempt
  FROM gap.novel_revlog nr
  GROUP BY nr.item_id
),
violations AS (
  -- (a) is_holdout outside {0,1}. is_holdout is set at insert, never repaired.
  SELECT 'a_is_holdout_out_of_range' AS check_name,
         ni.id                       AS offending_id,
         'is_holdout=' || ni.is_holdout AS detail
  FROM gap.novel_items ni
  WHERE ni.is_holdout NOT IN (0, 1)

  UNION ALL

  -- (b) One source_id spanning the holdout boundary: the same generator's output
  --     appears on both sides of the split, so held-out content leaks into practice.
  SELECT 'b_source_spans_holdout' AS check_name,
         ni.source_id             AS offending_id,
         'source_id has both holdout and non-holdout items' AS detail
  FROM gap.novel_items ni
  GROUP BY ni.source_id
  HAVING SUM(CASE WHEN ni.is_holdout = 1 THEN 1 ELSE 0 END) > 0
     AND SUM(CASE WHEN ni.is_holdout = 0 THEN 1 ELSE 0 END) > 0

  UNION ALL

  -- (c) A held-out item practiced too early: its earliest novel attempt lands at or
  --     before the concept's first card exposure. Held-out items must be terminal,
  --     not practiced during study. Emitted per (item, concept) that violates.
  SELECT 'c_holdout_practiced_early' AS check_name,
         ni.id                       AS offending_id,
         'concept_id=' || nic.concept_id
           || ' first_attempt=' || ifn.first_attempt
           || ' <= first_exposure=' || ce.first_exposure AS detail
  FROM gap.novel_items ni
  JOIN gap.novel_item_concepts nic ON nic.item_id = ni.id
  JOIN item_first_novel ifn        ON ifn.item_id = ni.id
  JOIN concept_exposure ce         ON ce.concept_id = nic.concept_id
  WHERE ni.is_holdout = 1
    AND ifn.first_attempt <= ce.first_exposure

  UNION ALL

  -- (d) An arm assigned after the concept's first exposure. assigned_ms must
  --     predate first exposure, else the scheduler's behaviour could steer assignment.
  SELECT 'd_arm_assigned_after_exposure' AS check_name,
         a.concept_id                    AS offending_id,
         'assigned_ms=' || a.assigned_ms
           || ' > first_exposure=' || ce.first_exposure AS detail
  FROM gap.arms a
  JOIN concept_exposure ce ON ce.concept_id = a.concept_id
  WHERE a.assigned_ms > ce.first_exposure

  UNION ALL

  -- (e) Defensive: an arm row with no arm-legal value or an orphan concept_id.
  --     The schema CHECK-constrains arm and FK-references concepts, but a broken
  --     import could still land NULLs or dangling ids; flag them explicitly.
  SELECT 'e_arm_invalid_or_orphan' AS check_name,
         a.concept_id              AS offending_id,
         'arm=' || COALESCE(a.arm, 'NULL')
           || CASE WHEN NOT EXISTS (SELECT 1 FROM gap.concepts c WHERE c.id = a.concept_id)
                   THEN ' orphan_concept_id' ELSE '' END AS detail
  FROM gap.arms a
  WHERE a.arm IS NULL
     OR a.arm NOT IN ('gate', 'nogate', 'vanilla')
     OR a.concept_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM gap.concepts c WHERE c.id = a.concept_id)
)
SELECT check_name, offending_id, detail
FROM violations
ORDER BY check_name, offending_id;

-- ---------------------------------------------------------------------------
-- Statement 2: per-check summary. One row per check_name; 0 == clean.
--   check_names is the fixed roster so a clean run still emits every check at 0.
-- ---------------------------------------------------------------------------
WITH
concept_exposure AS (
  SELECT nc.concept_id AS concept_id,
         MIN(r.id)     AS first_exposure
  FROM main.revlog r
  JOIN main.cards c        ON c.id = r.cid
  JOIN main.notes n        ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
  GROUP BY nc.concept_id
),
item_first_novel AS (
  SELECT nr.item_id AS item_id,
         MIN(nr.id) AS first_attempt
  FROM gap.novel_revlog nr
  GROUP BY nr.item_id
),
violations AS (
  SELECT 'a_is_holdout_out_of_range' AS check_name, ni.id AS offending_id
  FROM gap.novel_items ni
  WHERE ni.is_holdout NOT IN (0, 1)

  UNION ALL

  SELECT 'b_source_spans_holdout' AS check_name, ni.source_id AS offending_id
  FROM gap.novel_items ni
  GROUP BY ni.source_id
  HAVING SUM(CASE WHEN ni.is_holdout = 1 THEN 1 ELSE 0 END) > 0
     AND SUM(CASE WHEN ni.is_holdout = 0 THEN 1 ELSE 0 END) > 0

  UNION ALL

  SELECT 'c_holdout_practiced_early' AS check_name, ni.id AS offending_id
  FROM gap.novel_items ni
  JOIN gap.novel_item_concepts nic ON nic.item_id = ni.id
  JOIN item_first_novel ifn        ON ifn.item_id = ni.id
  JOIN concept_exposure ce         ON ce.concept_id = nic.concept_id
  WHERE ni.is_holdout = 1
    AND ifn.first_attempt <= ce.first_exposure

  UNION ALL

  SELECT 'd_arm_assigned_after_exposure' AS check_name, a.concept_id AS offending_id
  FROM gap.arms a
  JOIN concept_exposure ce ON ce.concept_id = a.concept_id
  WHERE a.assigned_ms > ce.first_exposure

  UNION ALL

  SELECT 'e_arm_invalid_or_orphan' AS check_name, a.concept_id AS offending_id
  FROM gap.arms a
  WHERE a.arm IS NULL
     OR a.arm NOT IN ('gate', 'nogate', 'vanilla')
     OR a.concept_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM gap.concepts c WHERE c.id = a.concept_id)
),
check_names(check_name) AS (
  VALUES ('a_is_holdout_out_of_range'),
         ('b_source_spans_holdout'),
         ('c_holdout_practiced_early'),
         ('d_arm_assigned_after_exposure'),
         ('e_arm_invalid_or_orphan')
)
SELECT cn.check_name                AS check_name,
       COALESCE(v.violation_count, 0) AS violation_count
FROM check_names cn
LEFT JOIN (
  SELECT check_name, COUNT(*) AS violation_count
  FROM violations
  GROUP BY check_name
) v ON v.check_name = cn.check_name
ORDER BY cn.check_name;
