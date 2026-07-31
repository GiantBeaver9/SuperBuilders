-- main_stub.sql — a MINIMAL stand-in for Anki's collection.anki2 schema.
--
-- This is NOT shipped and NOT the real collection. It exists only so the query
-- files under queries/ can be syntax- and column-checked by scripts/validate_sql.py:
-- the harness creates these tables in the `main` schema, ATTACHes a fresh gap.db
-- as `gap`, and runs each query against empty tables.
--
-- Columns mirror the real Anki tables the queries actually read
-- (notes.guid/tags, cards.nid/data, revlog.cid/ease/time). FSRS memory state
-- (stability `s`, difficulty `d`) is read out of cards.data as JSON — see
-- queries/CONVENTIONS.md for the single authoritative retrievability definition
-- and the assumption it rests on.

CREATE TABLE IF NOT EXISTS col (
  id     INTEGER PRIMARY KEY,
  crt    INTEGER NOT NULL,
  mod    INTEGER NOT NULL,
  scm    INTEGER NOT NULL,
  ver    INTEGER NOT NULL,
  dty    INTEGER NOT NULL,
  usn    INTEGER NOT NULL,
  ls     INTEGER NOT NULL,
  conf   TEXT NOT NULL,
  models TEXT NOT NULL,
  decks  TEXT NOT NULL,
  dconf  TEXT NOT NULL,
  tags   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id    INTEGER PRIMARY KEY,
  guid  TEXT NOT NULL,
  mid   INTEGER NOT NULL,
  mod   INTEGER NOT NULL,
  usn   INTEGER NOT NULL,
  tags  TEXT NOT NULL,          -- space-delimited, e.g. ' concept::1A.2 concept::1A.3 '
  flds  TEXT NOT NULL,
  sfld  TEXT,
  csum  INTEGER NOT NULL,
  flags INTEGER NOT NULL,
  data  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
  id     INTEGER PRIMARY KEY,
  nid    INTEGER NOT NULL,
  did    INTEGER NOT NULL,
  ord    INTEGER NOT NULL,
  mod    INTEGER NOT NULL,
  usn    INTEGER NOT NULL,
  type   INTEGER NOT NULL,
  queue  INTEGER NOT NULL,
  due    INTEGER NOT NULL,
  ivl    INTEGER NOT NULL,
  factor INTEGER NOT NULL,
  reps   INTEGER NOT NULL,
  lapses INTEGER NOT NULL,
  left   INTEGER NOT NULL,
  odue   INTEGER NOT NULL,
  odid   INTEGER NOT NULL,
  flags  INTEGER NOT NULL,
  data   TEXT NOT NULL          -- JSON; FSRS state at $.s (stability), $.d (difficulty)
);

CREATE TABLE IF NOT EXISTS revlog (
  id      INTEGER PRIMARY KEY,  -- epoch ms of the review == the exposure timestamp
  cid     INTEGER NOT NULL,     -- cards.id
  usn     INTEGER NOT NULL,
  ease    INTEGER NOT NULL,     -- the 1..4 grade (pre-reg's revlog.ease)
  ivl     INTEGER NOT NULL,
  lastIvl INTEGER NOT NULL,
  factor  INTEGER NOT NULL,
  time    INTEGER NOT NULL,     -- ms spent on the review
  type    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_revlog_cid ON revlog(cid, id);
CREATE INDEX IF NOT EXISTS ix_cards_nid  ON cards(nid);
CREATE INDEX IF NOT EXISTS ix_notes_guid ON notes(guid);
