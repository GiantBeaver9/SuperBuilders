-- terminal_novel_accuracy.sql
--
-- WHAT:  Secondary endpoint. Terminal novel accuracy on HELD-OUT items only
--        (gap.novel_items.is_holdout = 1). Per-arm mean novel accuracy and the
--        A - B contrast in percentage points (gate - nogate), with per-arm n
--        (novel attempts and distinct concepts).
--
-- DIRECTIONAL / UNDERPOWERED — DO NOT HEADLINE. The pre-registration reports
-- this endpoint as directional only, with a 90% interval of [0, +16] pp for the
-- A - B difference (predicted point +8 pp). The interval itself is computed
-- OUTSIDE SQL; this file produces the point estimate and the per-arm n that
-- feed it. It is never reported as a standalone headline result.
--
-- WHEN:  Analysis time, after §8 leakage check is reported clean. Read-only.
-- READS: gap.novel_revlog, gap.novel_items, gap.novel_item_concepts, gap.arms.
-- WRITES: nothing (pure SELECT).
--
-- Novel accuracy is attempt-level AVG(correct) per CONVENTIONS.md; the held-out
-- set (is_holdout = 1) is used ONLY for this terminal contrast, never to gate or
-- order. Contrast direction is A - B = gate - nogate; vanilla is the Arm C
-- sanity check reported in arm_c_sanity.sql.

WITH holdout_attempts AS (
  -- One row per held-out novel attempt, attributed to a concept and its arm.
  -- A held-out item can hit several concepts (novel_item_concepts), so an
  -- attempt is counted once per concept it exercises, under that concept's arm.
  SELECT
    a.arm         AS arm,
    nic.concept_id AS concept_id,
    nr.correct    AS correct
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni         ON ni.id = nr.item_id
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  JOIN gap.arms a                 ON a.concept_id = nic.concept_id
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
  'terminal_novel_accuracy_holdout' AS endpoint,
  -- Per-arm mean held-out novel accuracy, in percentage points.
  MAX(CASE WHEN arm = 'gate'   THEN mean_acc END) * 100.0 AS gate_mean_pct,
  MAX(CASE WHEN arm = 'nogate' THEN mean_acc END) * 100.0 AS nogate_mean_pct,
  -- Headline contrast: A - B = gate - nogate, in percentage points.
  (MAX(CASE WHEN arm = 'gate'   THEN mean_acc END)
   - MAX(CASE WHEN arm = 'nogate' THEN mean_acc END)) * 100.0 AS a_minus_b_pp,
  -- Per-arm n feeding the (external) 90% interval.
  MAX(CASE WHEN arm = 'gate'   THEN n_attempts END) AS gate_n_attempts,
  MAX(CASE WHEN arm = 'gate'   THEN n_concepts END) AS gate_n_concepts,
  MAX(CASE WHEN arm = 'nogate' THEN n_attempts END) AS nogate_n_attempts,
  MAX(CASE WHEN arm = 'nogate' THEN n_concepts END) AS nogate_n_concepts
FROM per_arm;
