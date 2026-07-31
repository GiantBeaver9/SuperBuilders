-- abstain_rule.sql — the dashboard abstain rule (traceability §7, POV-1).
--
-- WHAT: The dashboard refuses to show a Performance score for any concept with
--   fewer than 8 novel attempts; it shows coverage % (progress toward the
--   threshold) instead. Statement 1 emits the per-concept dashboard row.
--   Statement 2 is the "how I'll know it was wrong" check from the traceability
--   row: it compares mean novel accuracy of concepts BELOW the abstain line
--   (<8 attempts) against those AT/ABOVE it (>=8). If the two means are equal,
--   the threshold tracks nothing and the honesty rule is theatre.
-- WHEN: Run any time the dashboard is rendered / audited. Read-only reporting.
-- READS: gap.concepts, gap.novel_revlog, gap.novel_items, gap.novel_item_concepts.
-- WRITES: nothing. Read-only. No bind parameters.
--
-- ATTEMPT-COUNT DEFINITION: novel_attempt_count counts PRACTICE attempts only
--   (ni.is_holdout = 0). This matches what a live dashboard would show — the
--   held-out set is terminal and is never surfaced during study — and it keeps
--   the abstain gate off the terminal endpoint. The Performance score below is
--   likewise practice novel accuracy (AVG of correct over is_holdout=0 items).
--   Threshold for scoring: >= 8 practice novel attempts.

-- ---------------------------------------------------------------------------
-- Statement 1: per-concept dashboard row.
-- ---------------------------------------------------------------------------
WITH practice_attempts AS (
  -- Per concept: count and accuracy of PRACTICE (is_holdout=0) novel attempts.
  -- An item hitting several concepts contributes to each linked concept.
  SELECT nic.concept_id AS concept_id,
         COUNT(*)       AS n_attempts,
         AVG(nr.correct) AS novel_accuracy
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  WHERE ni.is_holdout = 0
  GROUP BY nic.concept_id
)
SELECT
  c.id   AS concept_id,
  c.code AS code,
  COALESCE(pa.n_attempts, 0) AS novel_attempt_count,
  CASE WHEN COALESCE(pa.n_attempts, 0) >= 8 THEN 1 ELSE 0 END AS has_performance_score,
  -- Scored only when at/above the line; otherwise abstain (NULL).
  CASE WHEN COALESCE(pa.n_attempts, 0) >= 8 THEN pa.novel_accuracy
       ELSE NULL END AS performance_score,
  -- Shown only when abstaining: progress toward the 8-attempt threshold, capped
  -- at 100%. NULLIF guards the (constant) denominator defensively.
  CASE WHEN COALESCE(pa.n_attempts, 0) >= 8 THEN NULL
       ELSE ROUND(
              MIN(100.0,
                  COALESCE(pa.n_attempts, 0) * 100.0 / NULLIF(8.0, 0)),
              1)
  END AS coverage_pct
FROM gap.concepts c
LEFT JOIN practice_attempts pa ON pa.concept_id = c.id
ORDER BY c.id;

-- ---------------------------------------------------------------------------
-- Statement 2: does the threshold track anything?
--   Mean of per-concept practice novel accuracy, split at the abstain line.
--   Only concepts with >=1 practice attempt have an accuracy to average, so the
--   comparison is over concepts that actually have a novel signal. If
--   mean_acc_below == mean_acc_atabove (diff ~ 0), the 8-attempt line separates
--   nothing.
-- ---------------------------------------------------------------------------
WITH practice_attempts AS (
  SELECT nic.concept_id AS concept_id,
         COUNT(*)       AS n_attempts,
         AVG(nr.correct) AS novel_accuracy
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  WHERE ni.is_holdout = 0
  GROUP BY nic.concept_id
)
SELECT
  AVG(CASE WHEN n_attempts < 8  THEN novel_accuracy END) AS mean_acc_below,
  AVG(CASE WHEN n_attempts >= 8 THEN novel_accuracy END) AS mean_acc_atabove,
  AVG(CASE WHEN n_attempts < 8  THEN novel_accuracy END)
    - AVG(CASE WHEN n_attempts >= 8 THEN novel_accuracy END) AS diff_below_minus_atabove
FROM practice_attempts;
