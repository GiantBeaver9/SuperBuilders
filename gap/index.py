"""Membership index — rebuild `gap.note_concepts` from note tags.

`gap.note_concepts` is a *derived* index: the source of truth for concept
membership is the note tag `concept::<code>` (which syncs for free on Anki's
existing note sync). This module is a thin wrapper over the committed SQL
(`queries/01_open/rebuild_note_concepts.sql`), which does a full
DELETE + INSERT rebuild and is idempotent. The SQL is math-free (a padded
`LIKE` match), so it is safe to run live inside Anki.
"""
from __future__ import annotations

from typing import Any


def rebuild(gapdb: Any) -> int:
    """Rebuild `gap.note_concepts` from `main.notes.tags`.

    Runs `queries/01_open/rebuild_note_concepts.sql` verbatim (read-only on
    `main.*`, writes only `gap.note_concepts`) and returns the resulting row
    count. Idempotent: the SQL fully rebuilds the index, so re-running yields
    the same rows.
    """
    gapdb.run_file("queries/01_open/rebuild_note_concepts.sql")
    gapdb.commit()
    return int(gapdb.scalar("SELECT COUNT(*) FROM gap.note_concepts") or 0)
