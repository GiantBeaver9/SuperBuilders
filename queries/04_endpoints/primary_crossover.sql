-- primary_crossover.sql — PRIMARY ENDPOINT: Arm x exposure-bucket interaction
-- on novel-item accuracy (the committed crossover / sign-flip).
--
-- WHAT IT IS
--   Two read-only result sets:
--     (a) the attempt-level analysis table — one row per PRACTICE novel attempt
--         (is_holdout = 0) per concept the attempt's item touches, carrying the
--         concept's exposure count AS OF that attempt and its '1-4' / '5+' bucket.
--         This is the analysis-ready table the mixed-effects logistic regression
--         (random intercepts for student and concept) consumes OUTSIDE SQL.
--     (b) the descriptive crossover contrast — mean novel accuracy by
--         arm x exposure_bucket with per-cell n, plus the A-B (gate - nogate)
--         difference in percentage points per bucket, so the committed sign flip
--         (negative pp at 1-4, positive pp at 5+) is directly readable.
--
-- WHEN IT RUNS
--   Analysis time, after novel attempts and arm assignments exist. Descriptive
--   only — the inferential model (the interaction test) runs outside SQL on (a).
--
-- ONE STUDENT PER COLLECTION
--   gap.db is one student. Every row here is that student. concept_id and the
--   item-level ids (novel_revlog_id, item_id) are emitted so an external loader
--   can UNION per-student exports and add a student id before fitting the model.
--
-- READS  (read-only; writes NOTHING):
--   main.revlog, main.cards, main.notes   (exposure counting, concept membership)
--   gap.novel_revlog, gap.novel_items, gap.novel_item_concepts   (novel attempts)
--   gap.note_concepts, gap.concepts, gap.arms                    (concept map, weight, arm)
--
-- FSRS STATE
--   This endpoint uses correct / latency / exposure count only. It reads NO FSRS
--   memory state (cards.data.$.s / $.d), so there is no `card_state` CTE here —
--   per the contract, that CTE exists only to isolate FSRS state reads, and there
--   are none.
--
-- DEFINITIONS (per queries/CONVENTIONS.md, obeyed exactly):
--   exposure count as-of a novel attempt =
--     COUNT(main.revlog r) on the concept's cards with r.id <= novel_revlog.id
--   exposure bucket = CASE WHEN exposure_count <= 4 THEN '1-4' ELSE '5+' END
--   practice novel accuracy = AVG(correct) over items with is_holdout = 0
--   contrast sign = A - B = gate - nogate

-- (a) ATTEMPT-LEVEL ANALYSIS TABLE — one row per practice novel attempt x concept.
WITH practice_novel AS (
  -- Practice novel attempts only (is_holdout = 0). Held-out is the terminal
  -- endpoint, computed elsewhere; it never feeds the crossover.
  SELECT nr.id      AS novel_revlog_id,
         nr.item_id AS item_id,
         nic.concept_id AS concept_id,
         nr.correct AS correct,
         nr.time    AS latency_ms
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id AND ni.is_holdout = 0
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
),
concept_cards AS (
  -- A card belongs to a concept via its note's guid (CONVENTIONS "Concept membership").
  SELECT nc.concept_id AS concept_id, c.id AS card_id
  FROM main.cards c
  JOIN main.notes n         ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
),
attempt_exposure AS (
  SELECT pn.novel_revlog_id,
         pn.item_id,
         pn.concept_id,
         pn.correct,
         pn.latency_ms,
         (SELECT COUNT(*)
            FROM concept_cards cc
            JOIN main.revlog r ON r.cid = cc.card_id
           WHERE cc.concept_id = pn.concept_id
             AND r.id <= pn.novel_revlog_id) AS exposure_count
  FROM practice_novel pn
)
SELECT ae.novel_revlog_id,
       ae.item_id,
       ae.concept_id,
       a.arm,
       ae.exposure_count,
       CASE WHEN ae.exposure_count <= 4 THEN '1-4' ELSE '5+' END AS exposure_bucket,
       ae.correct,
       ae.latency_ms,
       co.weight AS weight
FROM attempt_exposure ae
JOIN gap.arms a      ON a.concept_id = ae.concept_id
JOIN gap.concepts co ON co.id = ae.concept_id
ORDER BY ae.concept_id, ae.novel_revlog_id;

-- (b) DESCRIPTIVE CROSSOVER CONTRAST — one row per exposure_bucket.
--     gate/nogate/vanilla mean accuracy (pct) + per-cell n, and the committed
--     A-B difference (gate - nogate) in percentage points. Read the sign of
--     diff_pp_gate_minus_nogate: negative at '1-4', positive at '5+' == flip.
WITH practice_novel AS (
  SELECT nr.id AS novel_revlog_id, nr.item_id AS item_id,
         nic.concept_id AS concept_id, nr.correct AS correct
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id AND ni.is_holdout = 0
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
),
concept_cards AS (
  SELECT nc.concept_id AS concept_id, c.id AS card_id
  FROM main.cards c
  JOIN main.notes n         ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
),
labelled AS (
  SELECT CASE
           WHEN (SELECT COUNT(*)
                   FROM concept_cards cc
                   JOIN main.revlog r ON r.cid = cc.card_id
                  WHERE cc.concept_id = pn.concept_id
                    AND r.id <= pn.novel_revlog_id) <= 4
           THEN '1-4' ELSE '5+' END AS exposure_bucket,
         a.arm     AS arm,
         pn.correct AS correct
  FROM practice_novel pn
  JOIN gap.arms a ON a.concept_id = pn.concept_id
),
cell AS (
  SELECT exposure_bucket, arm, COUNT(*) AS n, SUM(correct) AS s
  FROM labelled
  GROUP BY exposure_bucket, arm
)
SELECT
  exposure_bucket,
  SUM(CASE WHEN arm = 'gate'    THEN n ELSE 0 END) AS n_gate,
  SUM(CASE WHEN arm = 'nogate'  THEN n ELSE 0 END) AS n_nogate,
  SUM(CASE WHEN arm = 'vanilla' THEN n ELSE 0 END) AS n_vanilla,
  100.0 * SUM(CASE WHEN arm = 'gate'    THEN s ELSE 0 END)
        / NULLIF(SUM(CASE WHEN arm = 'gate'    THEN n ELSE 0 END), 0) AS acc_gate_pct,
  100.0 * SUM(CASE WHEN arm = 'nogate'  THEN s ELSE 0 END)
        / NULLIF(SUM(CASE WHEN arm = 'nogate'  THEN n ELSE 0 END), 0) AS acc_nogate_pct,
  100.0 * SUM(CASE WHEN arm = 'vanilla' THEN s ELSE 0 END)
        / NULLIF(SUM(CASE WHEN arm = 'vanilla' THEN n ELSE 0 END), 0) AS acc_vanilla_pct,
  ( 100.0 * SUM(CASE WHEN arm = 'gate'   THEN s ELSE 0 END)
          / NULLIF(SUM(CASE WHEN arm = 'gate'   THEN n ELSE 0 END), 0) )
  - ( 100.0 * SUM(CASE WHEN arm = 'nogate' THEN s ELSE 0 END)
            / NULLIF(SUM(CASE WHEN arm = 'nogate' THEN n ELSE 0 END), 0) )
    AS diff_pp_gate_minus_nogate
FROM cell
GROUP BY exposure_bucket
ORDER BY exposure_bucket;
