-- throughput_cost.sql
--
-- WHAT:  Secondary endpoint. Cost in throughput. The pre-registration predicts
--        Arm A retires 25-35% FEWER concepts than Arm B in equal study minutes.
--        This is a stated price, not a failure. gap.db has NO persisted
--        `retired` flag, so retirement is DERIVED here per arm:
--          - gate (A):    concept is retired when PRACTICE novel accuracy
--                         (is_holdout = 0) >= 0.7.
--          - nogate (B):  mastery-only rule (novel gate feature off).
--          - vanilla (C): same mastery-only rule (unmodified Anki has no novel
--                         gate, so it can only retire on card mastery).
--
--        MASTERY-ONLY RETIREMENT (nogate + vanilla), ASSUMPTION:
--          A concept is retired when ALL of its cards that carry an FSRS memory
--          state have retrievability R >= 0.9, AND it has at least one such card.
--          Cards with no memory state (never reviewed) contribute no R per
--          CONVENTIONS.md and are excluded from the "all cards" test; a concept
--          with zero reviewed cards cannot be retired. The 0.9 R threshold is
--          the assumption to adjust if a different mastery bar is intended.
--
-- WHEN:  Analysis time. Read-only.
-- READS: main.cards, main.notes, main.revlog (read-only), gap.note_concepts,
--        gap.novel_revlog, gap.novel_items, gap.novel_item_concepts, gap.arms.
-- WRITES: nothing (pure SELECT).
--
-- Study minutes per arm = SUM(main.revlog.time) over the arm's concepts / 60000.0.
-- Contrast direction is A - B = gate - nogate.

WITH card_state AS (
  -- ISOLATED FSRS READS. If the collection stores FSRS memory state elsewhere,
  -- change ONLY the two json_extract calls here (see CONVENTIONS.md).
  SELECT
    c.id          AS card_id,
    nc.concept_id AS concept_id,
    json_extract(c.data, '$.s') AS stability,          -- S
    (SELECT MAX(r.id) FROM main.revlog r WHERE r.cid = c.id) AS last_review_ms
  FROM main.cards c
  JOIN main.notes n         ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
),
card_retrievability AS (
  -- FSRS-5 retrievability R(t) = (1 + FACTOR * t / S) ^ DECAY,
  -- DECAY = -0.5, FACTOR = 19.0/81.0, t = elapsed days since last review.
  -- Only cards that carry a memory state and have been reviewed have an R.
  SELECT
    card_id,
    concept_id,
    pow(
      1.0 + (19.0 / 81.0)
            * ((unixepoch('subsec') * 1000 - last_review_ms) / 86400000.0)
            / stability,
      -0.5
    ) AS r
  FROM card_state
  WHERE stability IS NOT NULL
    AND last_review_ms IS NOT NULL
),
mastery_retired AS (
  -- nogate + vanilla: every reviewed card of the concept has R >= 0.9,
  -- and there is at least one reviewed card (GROUP BY guarantees >= 1 row).
  SELECT concept_id
  FROM card_retrievability
  GROUP BY concept_id
  HAVING MIN(r) >= 0.9
),
practice_novel AS (
  -- Practice (is_holdout = 0) novel accuracy per concept — drives the gate.
  SELECT
    nic.concept_id AS concept_id,
    AVG(nr.correct) AS practice_acc
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  WHERE ni.is_holdout = 0
  GROUP BY nic.concept_id
),
gate_retired AS (
  -- gate: practice novel accuracy >= 0.7.
  SELECT concept_id
  FROM practice_novel
  WHERE practice_acc >= 0.7
),
concept_minutes AS (
  -- Study time per concept = SUM(main.revlog.time) over the concept's cards.
  SELECT
    nc.concept_id AS concept_id,
    SUM(r.time)   AS total_time_ms
  FROM main.revlog r
  JOIN main.cards c         ON c.id = r.cid
  JOIN main.notes n         ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
  GROUP BY nc.concept_id
),
concept_retired AS (
  -- Per concept: is it retired under its own arm's rule?
  SELECT
    a.concept_id,
    a.arm,
    CASE a.arm
      WHEN 'gate'    THEN (a.concept_id IN (SELECT concept_id FROM gate_retired))
      WHEN 'nogate'  THEN (a.concept_id IN (SELECT concept_id FROM mastery_retired))
      WHEN 'vanilla' THEN (a.concept_id IN (SELECT concept_id FROM mastery_retired))
    END AS is_retired
  FROM gap.arms a
),
per_arm AS (
  SELECT
    cr.arm,
    COALESCE(SUM(cr.is_retired), 0)          AS retired_concept_count,
    COUNT(*)                                 AS total_concepts,
    COALESCE(SUM(cm.total_time_ms), 0) / 60000.0 AS study_minutes
  FROM concept_retired cr
  LEFT JOIN concept_minutes cm ON cm.concept_id = cr.concept_id
  GROUP BY cr.arm
),
per_arm_rates AS (
  SELECT
    arm,
    retired_concept_count,
    total_concepts,
    study_minutes,
    -- retired per hour = retired / (minutes / 60); guard empty tables.
    retired_concept_count / (NULLIF(study_minutes, 0) / 60.0) AS retired_per_hour,
    -- retired per minute, for the A vs B headline contrast.
    retired_concept_count / NULLIF(study_minutes, 0)          AS retired_per_min
  FROM per_arm
)
SELECT
  -- Per-arm throughput.
  MAX(CASE WHEN arm = 'gate'    THEN retired_concept_count END) AS gate_retired,
  MAX(CASE WHEN arm = 'gate'    THEN total_concepts        END) AS gate_total_concepts,
  MAX(CASE WHEN arm = 'gate'    THEN study_minutes         END) AS gate_study_minutes,
  MAX(CASE WHEN arm = 'gate'    THEN retired_per_hour       END) AS gate_retired_per_hour,
  MAX(CASE WHEN arm = 'nogate'  THEN retired_concept_count END) AS nogate_retired,
  MAX(CASE WHEN arm = 'nogate'  THEN total_concepts        END) AS nogate_total_concepts,
  MAX(CASE WHEN arm = 'nogate'  THEN study_minutes         END) AS nogate_study_minutes,
  MAX(CASE WHEN arm = 'nogate'  THEN retired_per_hour       END) AS nogate_retired_per_hour,
  MAX(CASE WHEN arm = 'vanilla' THEN retired_concept_count END) AS vanilla_retired,
  MAX(CASE WHEN arm = 'vanilla' THEN total_concepts        END) AS vanilla_total_concepts,
  MAX(CASE WHEN arm = 'vanilla' THEN study_minutes         END) AS vanilla_study_minutes,
  MAX(CASE WHEN arm = 'vanilla' THEN retired_per_hour       END) AS vanilla_retired_per_hour,
  -- Headline contrast: A vs B percent difference in retired-per-minute,
  -- ((A - B) / B * 100). Predicted -25% to -35% (Arm A retires fewer).
  (MAX(CASE WHEN arm = 'gate' THEN retired_per_min END)
   - MAX(CASE WHEN arm = 'nogate' THEN retired_per_min END))
  / NULLIF(MAX(CASE WHEN arm = 'nogate' THEN retired_per_min END), 0) * 100.0
    AS pct_diff_retired_per_min_A_vs_B
FROM per_arm_rates;
