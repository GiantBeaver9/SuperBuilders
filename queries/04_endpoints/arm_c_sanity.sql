-- arm_c_sanity.sql
--
-- WHAT:  Secondary endpoint / sanity check. Compares Arm A (gate) against
--        Arm C (vanilla, unmodified Anki) on TERMINAL held-out novel accuracy
--        (gap.novel_items.is_holdout = 1): per-arm mean and the A - C
--        difference in percentage points, with per-arm n.
--
--        C ~= A  =>  the app does not beat unmodified Anki, and POV-1 does not
--        justify the build. This is the sanity gate on the whole thesis.
--
-- DIRECTIONAL / UNDERPOWERED — like terminal_novel_accuracy.sql, the held-out
-- contrast is reported directionally, never as a standalone headline.
--
-- WHEN:  Analysis time, after §8 leakage check is reported clean. Read-only.
-- READS: gap.novel_revlog, gap.novel_items, gap.novel_item_concepts, gap.arms.
-- WRITES: nothing (pure SELECT).
--
-- Novel accuracy is attempt-level AVG(correct) per CONVENTIONS.md; held-out
-- (is_holdout = 1) is used ONLY for this terminal contrast, never to gate/order.

WITH holdout_attempts AS (
  -- One row per held-out novel attempt, attributed to a concept and its arm.
  -- An item can hit several concepts, so an attempt is counted once per concept
  -- it exercises, under that concept's arm.
  SELECT
    a.arm          AS arm,
    nic.concept_id AS concept_id,
    nr.correct     AS correct
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  JOIN gap.arms a                  ON a.concept_id = nic.concept_id
  WHERE ni.is_holdout = 1
),
per_arm AS (
  SELECT
    arm,
    AVG(correct)               AS mean_acc,      -- 0..1
    COUNT(*)                   AS n_attempts,
    COUNT(DISTINCT concept_id) AS n_concepts
  FROM holdout_attempts
  GROUP BY arm
)
SELECT
  'arm_c_sanity_holdout' AS endpoint,
  -- Per-arm mean held-out novel accuracy, in percentage points.
  MAX(CASE WHEN arm = 'gate'    THEN mean_acc END) * 100.0 AS gate_mean_pct,
  MAX(CASE WHEN arm = 'vanilla' THEN mean_acc END) * 100.0 AS vanilla_mean_pct,
  -- Sanity contrast: A - C = gate - vanilla, in percentage points.
  -- Near zero => app does not beat unmodified Anki => POV-1 does not justify build.
  (MAX(CASE WHEN arm = 'gate'    THEN mean_acc END)
   - MAX(CASE WHEN arm = 'vanilla' THEN mean_acc END)) * 100.0 AS a_minus_c_pp,
  MAX(CASE WHEN arm = 'gate'    THEN n_attempts END) AS gate_n_attempts,
  MAX(CASE WHEN arm = 'gate'    THEN n_concepts END) AS gate_n_concepts,
  MAX(CASE WHEN arm = 'vanilla' THEN n_attempts END) AS vanilla_n_attempts,
  MAX(CASE WHEN arm = 'vanilla' THEN n_concepts END) AS vanilla_n_concepts
FROM per_arm;
