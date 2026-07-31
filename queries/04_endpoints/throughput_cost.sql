-- throughput_cost.sql
--
-- WHAT:  Secondary endpoint. Cost in throughput. The pre-registration predicts
--        Arm A retires 25-35% FEWER concepts than Arm B in equal study minutes.
--        This is a stated price, not a failure.
--
--        Retirement is the app's own PERSISTED signal (schema v2): a concept is
--        retired iff it has a row in gap.retirements. The app writes that row
--        when the concept's arm-specific rule fires:
--          - gate (A):    trigger 'novel_gate'   (practice novel accuracy >= 0.7)
--          - nogate (B):  trigger 'card_mastery'  (mastery-only rule, gate off)
--          - vanilla (C): trigger 'anki_default'  (sidecar observes unmodified
--                         Anki's graduation/maturity; never writes main.*)
--        This query no longer reconstructs retirement from card/novel state —
--        it counts ground truth. `trigger` is available if a by-rule breakdown
--        is wanted, but retirement itself is just row-presence.
--
-- WHEN:  Analysis time. Read-only.
-- READS: gap.arms, gap.retirements; main.revlog/cards/notes + gap.note_concepts
--        for study minutes. Reads nothing from FSRS memory state.
-- WRITES: nothing (pure SELECT).
--
-- Study minutes per arm = SUM(main.revlog.time) over the arm's concepts / 60000.0.
-- Contrast direction is A - B = gate - nogate.

WITH concept_minutes AS (
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
  -- Per concept: assigned arm + whether the app recorded a retirement row.
  SELECT
    a.concept_id,
    a.arm,
    (a.concept_id IN (SELECT concept_id FROM gap.retirements)) AS is_retired
  FROM gap.arms a
),
per_arm AS (
  SELECT
    cr.arm,
    COALESCE(SUM(cr.is_retired), 0)              AS retired_concept_count,
    COUNT(*)                                     AS total_concepts,
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
