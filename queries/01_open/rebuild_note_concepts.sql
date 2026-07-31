-- rebuild_note_concepts.sql — LIFECYCLE SETUP
--
-- WHAT:  Rebuilds the derived index gap.note_concepts, the (note guid, concept)
--        membership map. Membership is authored as a note tag `concept::<code>`
--        (see schema/gap.sql); this table is a cache of that tag, never the
--        source of truth.
-- WHEN:  At every backend open, before any query that joins through membership.
-- READS: main.notes (guid, tags) and gap.concepts (id, code).
-- WRITES: gap.note_concepts only. Read-only on main.
-- IDEMPOTENT: full DELETE + INSERT, so re-running yields the same rows.

-- Drop the whole derived index; it is rebuilt from scratch below.
DELETE FROM gap.note_concepts;

-- One row per (note, concept) pair whose tag string contains `concept::<code>`.
-- Anki stores tags space-delimited with surrounding spaces (e.g.
-- ' concept::1A.2 concept::1A.3 '). We pad the stored tags with one leading and
-- one trailing space, then LIKE-match the space-bounded tag ' concept::<code> '.
-- The bounding spaces make the match exact per tag: code '1A.2' will not match
-- the tag ' concept::1A.20 ' because the trailing space differs. Only codes that
-- exist in gap.concepts can join, so unknown tags are ignored.
INSERT INTO gap.note_concepts (guid, concept_id)
SELECT n.guid, c.id
FROM main.notes n
JOIN gap.concepts c
  ON (' ' || n.tags || ' ') LIKE ('% concept::' || c.code || ' %');
