-- points_at_stake.sql — STUDY-TIME QUEUE ordering (Rust points-at-stake queue).
--
-- WHAT:  Orders the due-card queue by the gap between what the card score
--        implies (card_mastery, FSRS retrievability) and what novel-item
--        accuracy actually shows, scaled by exam weight:
--            points_at_stake = (card_mastery - novel_accuracy) * exam_weight
--        Largest gap first. This is the traceability row "Rust points-at-stake
--        queue" in PREREGISTRATION.md.
-- WHEN:  During study, to build/re-order the due queue for the current moment.
-- READS: main.cards, main.notes, main.revlog (read-only);
--        gap.note_concepts, gap.concepts, gap.novel_revlog,
--        gap.novel_item_concepts, gap.novel_items.
-- WRITES: nothing. Pure SELECT, read-only.
--
-- Conventions (queries/CONVENTIONS.md):
--   * card_mastery(concept) = mean FSRS-5 retrievability across the concept's
--     cards that have a memory state (cards with no reviews contribute no R).
--   * novel_accuracy = PRACTICE novel accuracy, is_holdout = 0 ONLY. The
--     held-out set (is_holdout = 1) must never influence queue ordering.
--   * exam_weight = gap.concepts.weight.
--   * "now" is (unixepoch('subsec') * 1000), epoch-ms, matching Anki's ids.
--   * All FSRS state json_extract reads are isolated in the card_state CTE.

WITH
-- The single json_extract point for FSRS memory state (CONVENTIONS assumption:
-- state lives at cards.data.$.s / $.d; if the collection stores it elsewhere,
-- change ONLY the two json_extract calls here). Elapsed time is measured from
-- the card's last review (MAX(revlog.id)); the JOIN drops cards with no reviews,
-- so only cards that actually have a memory state reach retrievability.
card_state AS (
  SELECT
    c.id                         AS card_id,
    json_extract(c.data, '$.s')  AS stability,
    json_extract(c.data, '$.d')  AS difficulty,
    MAX(r.id)                    AS last_review_ms
  FROM main.cards c
  JOIN main.revlog r ON r.cid = c.id
  GROUP BY c.id
),

-- FSRS-5 retrievability per card:
--   R(t) = (1 + FACTOR * t / S) ^ DECAY,  DECAY = -0.5, FACTOR = 19/81,
--   t = elapsed days since last review = (now_ms - last_review_ms) / 86400000.
card_retrievability AS (
  SELECT
    cs.card_id,
    pow(
      1.0 + (19.0 / 81.0)
        * (((unixepoch('subsec') * 1000) - cs.last_review_ms) / 86400000.0)
        / cs.stability,
      -0.5
    ) AS r
  FROM card_state cs
  WHERE cs.stability IS NOT NULL
    AND cs.stability > 0
),

-- card_mastery(concept): mean retrievability across the concept's cards that
-- have a memory state. Concept membership is card -> note.guid -> note_concepts.
concept_mastery AS (
  SELECT
    nc.concept_id,
    AVG(cr.r) AS card_mastery
  FROM card_retrievability cr
  JOIN main.cards c          ON c.id  = cr.card_id
  JOIN main.notes n          ON n.id  = c.nid
  JOIN gap.note_concepts nc  ON nc.guid = n.guid
  GROUP BY nc.concept_id
),

-- PRACTICE novel accuracy per concept (is_holdout = 0 only). The held-out set
-- is intentionally excluded so it cannot influence ordering.
concept_novel AS (
  SELECT
    nic.concept_id,
    AVG(nr.correct) AS novel_accuracy
  FROM gap.novel_revlog nr
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
  JOIN gap.novel_items ni          ON ni.id       = nr.item_id
  WHERE ni.is_holdout = 0
  GROUP BY nic.concept_id
),

-- Due cards, mapped to their concept(s).
-- ASSUMPTION (portable "due"): a card is due when it is a review or day-learning
-- card (queue IN (2,3)) whose due value is at or before "now". Anki's real
-- cards.due units differ by queue (review/day-learn = day number since
-- collection creation; intraday learning = epoch seconds), so comparing due to
-- epoch-ms is a deliberately simplified, portable proxy documented here rather
-- than a faithful reproduction of Anki's per-queue due arithmetic. Adjust this
-- WHERE clause if exact Anki due semantics are required.
due_cards AS (
  SELECT
    c.id          AS card_id,
    nc.concept_id AS concept_id
  FROM main.cards c
  JOIN main.notes n         ON n.id   = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
  WHERE c.queue IN (2, 3)
    AND c.due <= (unixepoch('subsec') * 1000)
)

SELECT
  dc.card_id                                                    AS card_id,
  con.id                                                        AS concept_id,
  con.code                                                      AS concept_code,
  cm.card_mastery                                               AS card_mastery,
  COALESCE(cnv.novel_accuracy, 0.0)                             AS novel_accuracy,
  con.weight                                                    AS weight,
  (COALESCE(cm.card_mastery, 0.0) - COALESCE(cnv.novel_accuracy, 0.0))
    * con.weight                                                AS points_at_stake
FROM due_cards dc
JOIN gap.concepts con        ON con.id = dc.concept_id
LEFT JOIN concept_mastery cm ON cm.concept_id = dc.concept_id
LEFT JOIN concept_novel cnv  ON cnv.concept_id = dc.concept_id
ORDER BY points_at_stake DESC;
