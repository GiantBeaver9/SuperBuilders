-- gap.db — sidecar, NOT collection.anki2.
-- ATTACHed as `gap` at backend open. Lives beside the collection so it survives
-- a full download from AnkiWeb, which replaces collection.anki2 wholesale.
--
-- Rules:
--   * never write to main.revlog / main.cards / main.notes
--   * never touch main.col.scm (bumping it forces a full sync for the user)
--   * append-only: no review op mutates these, so Anki's undo never needs to know
--   * STRICT requires SQLite >= 3.37 (fine on bundled rusqlite and the AnkiDroid backend)
--
-- One collection == one student. No user_id; the analysis joins across exported files.

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val TEXT NOT NULL) STRICT;
INSERT OR IGNORE INTO meta(key, val) VALUES ('schema', '1');

CREATE TABLE IF NOT EXISTS concepts (
  id     INTEGER PRIMARY KEY,
  code   TEXT NOT NULL UNIQUE,          -- official outline code, e.g. '1A.2'
  name   TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0      -- exam weight, drives queue ordering
) STRICT;

-- Concept membership is authored as a note tag: `concept::1A.2`.
-- Tags sync for free on Anki's existing note sync, so this table is a derived
-- index rebuilt from main.notes.tags at open. Never the source of truth.
CREATE TABLE IF NOT EXISTS note_concepts (
  guid       TEXT NOT NULL,             -- notes.guid, survives export/import; nid does not
  concept_id INTEGER NOT NULL REFERENCES concepts(id),
  PRIMARY KEY (guid, concept_id)
) STRICT, WITHOUT ROWID;

-- Novel items are objects Anki has no notion of. They are not cards and must
-- never enter revlog. Own sync layer.
CREATE TABLE IF NOT EXISTS novel_items (
  id         INTEGER PRIMARY KEY,       -- epoch ms, mirrors Anki's id convention
  guid       TEXT NOT NULL UNIQUE,
  source_id  TEXT NOT NULL,             -- §11: untraceable AI output zeroes the section
  is_holdout INTEGER NOT NULL,          -- set at INSERT, never by a later UPDATE
  usn        INTEGER NOT NULL DEFAULT -1,
  mod        INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS novel_item_concepts (
  item_id    INTEGER NOT NULL REFERENCES novel_items(id),
  concept_id INTEGER NOT NULL REFERENCES concepts(id),
  PRIMARY KEY (item_id, concept_id)     -- a passage hits several concepts
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS novel_revlog (
  id      INTEGER PRIMARY KEY,          -- epoch ms
  item_id INTEGER NOT NULL REFERENCES novel_items(id),
  correct INTEGER NOT NULL,
  time    INTEGER NOT NULL,             -- ms, same units as main.revlog.time
  usn     INTEGER NOT NULL DEFAULT -1
) STRICT;

CREATE TABLE IF NOT EXISTS arms (
  concept_id  INTEGER PRIMARY KEY REFERENCES concepts(id),
  arm         TEXT NOT NULL CHECK (arm IN ('gate','nogate','vanilla')),
  assigned_ms INTEGER NOT NULL          -- must predate the concept's first exposure
) STRICT;

CREATE INDEX IF NOT EXISTS ix_novel_revlog_item ON novel_revlog(item_id, id);
CREATE INDEX IF NOT EXISTS ix_note_concepts_cid ON note_concepts(concept_id, guid);
CREATE INDEX IF NOT EXISTS ix_nic_concept       ON novel_item_concepts(concept_id, item_id);
