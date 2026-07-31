-- latency_dissociation.sql — SECONDARY ENDPOINT: latency dissociation.
--
-- WHAT IT IS
--   Tests the pre-registration's "Latency dissociation" clause: card review
--   latency (main.revlog.time) falls monotonically across exposures while
--   novel-item latency (gap.novel_revlog.time) does not. The claim is DEAD if
--   the two trajectories fall in lockstep — Pearson r > 0.8 within student.
--   Two read-only result sets:
--     (a) the paired series — per exposure index (and bucket): mean card review
--         time, mean novel latency, with each side's n, so both trajectories can
--         be plotted against a common x-axis.
--     (b) a single-row Pearson correlation between the two series across exposure
--         indices, computed in SQL by the sums form, labelled with the verdict
--         (r > 0.8 == lockstep == claim dead).
--
-- WHEN IT RUNS
--   Analysis time, after novel attempts and card reviews exist. Descriptive.
--
-- ONE STUDENT PER COLLECTION
--   gap.db is one student; every row is that student. The pre-registration's
--   lockstep threshold is "within student", which is exactly this per-collection
--   correlation. concept_id is not aggregated away in (a)'s inputs but the
--   plotted/correlated series are pooled across the student's concepts by
--   exposure index; an external loader repeats this per student.
--
-- READS  (read-only; writes NOTHING):
--   main.revlog, main.cards, main.notes                 (card latency, concept membership)
--   gap.novel_revlog, gap.novel_items, gap.novel_item_concepts,
--   gap.note_concepts                                    (novel latency, concept map)
--
-- FSRS STATE
--   Uses revlog.time and novel_revlog.time only — NO FSRS memory state
--   (cards.data.$.s / $.d) is read, so there is no `card_state` CTE here.
--
-- EXPOSURE-INDEX ALIGNMENT  (the one design decision to document)
--   The two sides are put on ONE common integer x-axis = number of card reviews
--   accumulated on the concept:
--     * CARD side:  the ordinal review number per concept — the i-th review of
--       any of the concept's cards, ordered by revlog.id
--       (ROW_NUMBER() OVER (PARTITION BY concept ORDER BY r.id)). Exposure index
--       i is the i-th exposure itself.
--     * NOVEL side: the exposure COUNT as-of the novel attempt — the number of
--       the concept's card reviews with r.id <= novel_revlog.id
--       (CONVENTIONS "Exposure count"). A novel attempt sits at exposure index i
--       when exactly i card reviews have accumulated.
--   Both are "how many card exposures on this concept by now", so index i is
--   directly comparable: the card review that MADE the count reach i vs. the
--   novel attempts taken WHILE the count equals i. The two per-index means are
--   then paired by i for the correlation. (Card ordinals start at 1; novel
--   attempts taken before any exposure sit at index 0 and simply have no card
--   counterpart to pair with — they drop out of the paired correlation.)
--   Novel side is restricted to PRACTICE attempts (is_holdout = 0), matching the
--   primary endpoint; held-out is reserved for the terminal contrast.
--
-- CORRELATION FORMULA (sums form, guarded against zero variance / empty tables):
--   r = (n*Sxy - Sx*Sy) / sqrt( (n*Sxx - Sx^2) * (n*Syy - Sy^2) )

-- (a) PAIRED SERIES — per exposure index: mean card time, mean novel latency, n each.
WITH card_concept_rev AS (
  -- Every (concept, card review) pair. A card belongs to a concept via note guid.
  SELECT nc.concept_id AS concept_id, r.id AS rid, r.time AS card_time
  FROM main.revlog r
  JOIN main.cards c         ON c.id = r.cid
  JOIN main.notes n         ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
),
card_indexed AS (
  SELECT concept_id, rid, card_time,
         ROW_NUMBER() OVER (PARTITION BY concept_id ORDER BY rid) AS exposure_index
  FROM card_concept_rev
),
card_series AS (
  SELECT exposure_index, AVG(card_time) AS mean_card_time, COUNT(*) AS n_card
  FROM card_indexed
  GROUP BY exposure_index
),
novel_attempts AS (
  -- Practice novel attempts, each placed at its exposure count as-of the attempt.
  SELECT nr.id AS novel_id, nic.concept_id AS concept_id, nr.time AS novel_time,
         (SELECT COUNT(*)
            FROM card_concept_rev cc
           WHERE cc.concept_id = nic.concept_id
             AND cc.rid <= nr.id) AS exposure_index
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id AND ni.is_holdout = 0
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
),
novel_series AS (
  SELECT exposure_index, AVG(novel_time) AS mean_novel_time, COUNT(*) AS n_novel
  FROM novel_attempts
  GROUP BY exposure_index
),
idx AS (
  SELECT exposure_index FROM card_series
  UNION
  SELECT exposure_index FROM novel_series
)
SELECT i.exposure_index,
       CASE WHEN i.exposure_index <= 4 THEN '1-4' ELSE '5+' END AS exposure_bucket,
       cs.mean_card_time,
       cs.n_card,
       ns.mean_novel_time,
       ns.n_novel
FROM idx i
LEFT JOIN card_series  cs ON cs.exposure_index = i.exposure_index
LEFT JOIN novel_series ns ON ns.exposure_index = i.exposure_index
ORDER BY i.exposure_index;

-- (b) PEARSON CORRELATION across exposure indices (single row + verdict).
WITH card_concept_rev AS (
  SELECT nc.concept_id AS concept_id, r.id AS rid, r.time AS card_time
  FROM main.revlog r
  JOIN main.cards c         ON c.id = r.cid
  JOIN main.notes n         ON n.id = c.nid
  JOIN gap.note_concepts nc ON nc.guid = n.guid
),
card_indexed AS (
  SELECT concept_id, rid, card_time,
         ROW_NUMBER() OVER (PARTITION BY concept_id ORDER BY rid) AS exposure_index
  FROM card_concept_rev
),
card_series AS (
  SELECT exposure_index, AVG(card_time) AS mean_card_time
  FROM card_indexed
  GROUP BY exposure_index
),
novel_attempts AS (
  SELECT nr.id AS novel_id, nic.concept_id AS concept_id, nr.time AS novel_time,
         (SELECT COUNT(*)
            FROM card_concept_rev cc
           WHERE cc.concept_id = nic.concept_id
             AND cc.rid <= nr.id) AS exposure_index
  FROM gap.novel_revlog nr
  JOIN gap.novel_items ni          ON ni.id = nr.item_id AND ni.is_holdout = 0
  JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
),
novel_series AS (
  SELECT exposure_index, AVG(novel_time) AS mean_novel_time
  FROM novel_attempts
  GROUP BY exposure_index
),
paired AS (
  -- One point per exposure index present on BOTH series: x = mean card time,
  -- y = mean novel latency.
  SELECT cs.mean_card_time AS x, ns.mean_novel_time AS y
  FROM card_series cs
  JOIN novel_series ns ON ns.exposure_index = cs.exposure_index
),
sums AS (
  SELECT COUNT(*)   AS n,
         SUM(x)     AS sx,
         SUM(y)     AS sy,
         SUM(x * x) AS sxx,
         SUM(y * y) AS syy,
         SUM(x * y) AS sxy
  FROM paired
),
corr AS (
  SELECT n,
         (n * sxy - sx * sy)
         / NULLIF(
             sqrt( NULLIF(n * sxx - sx * sx, 0) * NULLIF(n * syy - sy * sy, 0) ),
             0) AS r
  FROM sums
)
SELECT n AS n_points,
       r AS pearson_r,
       CASE
         WHEN r IS NULL          THEN 'undefined (no paired points or zero variance)'
         WHEN r > 0.8            THEN 'LOCKSTEP (r > 0.8): latency dissociation claim DEAD'
         ELSE                         'dissociated (r <= 0.8): claim survives'
       END AS verdict
FROM corr;
