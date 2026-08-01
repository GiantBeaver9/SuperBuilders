"""Engine facade — the single surface the aqt add-on calls.

The add-on layer (`addon/`) must stay decoupled from the individual engine
modules: it talks to *this* module only, and this module fans out to
`gap.index`, `gap.arms`, `gap.queue`, `gap.retirement`, `gap.novel`,
`gap.stats`. Keeping the fan-out here means the GUI never imports an engine
submodule directly, and the engine can be reshuffled without touching a hook.

Pure Python — **no `aqt` import**. Every function takes Anki's live
`Collection` (`col`) or, where noted, a `GapDB` directly, so the same code runs
headlessly in tests and inside Anki.

The engine submodules are imported lazily (inside the functions that need
them) rather than at module load. That keeps `import gap.service` cheap and,
more importantly, lets the facade import cleanly even while sibling modules are
still being built in parallel — a hook only touches a submodule at the moment
it is actually invoked, by which time it is bundled and present.
"""
from __future__ import annotations

from typing import Any

from gap.db import (
    GapDB,
    attach_gap,
    ensure_sidecar_schema,
    sidecar_path_for,
)

# --------------------------------------------------------------------------- #
# Per-collection GapDB cache
# --------------------------------------------------------------------------- #
# One collection == one student == one attached sidecar. ATTACHing the same
# sidecar twice onto the same live connection raises ("database gap is already
# in use"), so we cache the GapDB per collection path and reuse it. The cached
# entry pins the exact `col.db` proxy it was built on; if the collection is
# closed and reopened (a fresh `col.db`), the pin misses and we re-attach onto
# the new connection.
_CACHE: dict[str, GapDB] = {}


def open_for_collection(col: Any) -> GapDB:
    """Return the `GapDB` for `col`, attaching the sidecar once and caching it.

    Computes the sidecar path beside the collection, ensures its schema exists
    in the sidecar file (never through `col.db` — that would land the tables in
    the collection), ATTACHes it as `gap`, and wraps `col.db` in a `GapDB`.
    Idempotent per live connection.
    """
    path = col.path
    cached = _CACHE.get(path)
    if cached is not None and cached.db is col.db:
        return cached

    gap_path = sidecar_path_for(path)
    ensure_sidecar_schema(gap_path)
    try:
        attach_gap(col.db, gap_path)
    except Exception:
        # Already attached on this connection (re-open of the same col.db, or a
        # prior attach this session): the ATTACH is the only non-idempotent
        # step, and a duplicate is harmless.
        pass
    gapdb = GapDB(col.db)
    _CACHE[path] = gapdb
    return gapdb


def forget_collection(col: Any) -> None:
    """Drop the cached GapDB for `col` (call on profile/collection close)."""
    _CACHE.pop(getattr(col, "path", None), None)


# --------------------------------------------------------------------------- #
# Lifecycle hooks (called by the aqt layer)
# --------------------------------------------------------------------------- #
def on_profile_open(col: Any) -> None:
    """Startup sync: open the sidecar and bring the derived state up to date.

    Ensures a `gap.concepts` row exists for every `concept::<code>` tag, rebuilds
    the note->concept membership index from tags, then assigns arms to any
    still-unassigned concept. All three steps are idempotent.

    Order note: `ensure_concepts_from_tags` runs BEFORE `index.rebuild` because
    the rebuild joins `main.notes.tags` against `gap.concepts` — with no concept
    rows yet (a first-ever open) the index would come back empty. The concept
    rows must exist first for the very first rebuild to populate anything.
    """
    from gap import arms, index

    gapdb = open_for_collection(col)
    arms.ensure_concepts_from_tags(gapdb)
    index.rebuild(gapdb)
    arms.assign(gapdb)


def on_review_answered(col: Any, card_id: int, ease: int, time_ms: int) -> list[dict]:
    """Re-check retirement after Anki has logged a review; return new retirements.

    Grade (`ease`, 1-4) and latency (`time_ms`) are already persisted by Anki in
    `main.revlog` — this hook does not re-log them. It re-evaluates each arm's
    retirement rule now that the concept has one more exposure, and returns the
    list of concepts that newly retired on this evaluation (each a dict carrying
    at least `concept_id`), so the caller can suspend their cards and toast.

    `card_id`/`ease`/`time_ms` are accepted for a complete hook signature; the
    retirement rules read the persisted state rather than these arguments.
    """
    from gap import retirement

    gapdb = open_for_collection(col)
    return retirement.evaluate(gapdb)


def dashboard_payload(col: Any) -> dict:
    """The dashboard's data payload over this collection's sidecar."""
    from gap import stats

    gapdb = open_for_collection(col)
    return stats.dashboard_payload(gapdb)


def points_at_stake(col: Any) -> list[dict]:
    """Concepts ranked by points-at-stake (for the study-build menu action)."""
    from gap import queue

    gapdb = open_for_collection(col)
    return queue.points_at_stake(gapdb)


def ranked_card_ids(col: Any) -> list[int]:
    """Due card ids ordered by the points-at-stake gap, highest first."""
    from gap import queue

    gapdb = open_for_collection(col)
    return queue.ranked_card_ids(gapdb)


# --------------------------------------------------------------------------- #
# Novel-item helpers (the novel dialog goes through these, never gap.novel)
# --------------------------------------------------------------------------- #
def next_novel_item(col: Any, concept_id: int, holdout: bool = False) -> int | None:
    """The next novel item to probe for `concept_id` (fewest prior attempts)."""
    from gap import novel

    gapdb = open_for_collection(col)
    return novel.next_item(gapdb, concept_id, holdout=holdout)


def record_novel_attempt(col: Any, item_id: int, correct: bool, time_ms: int) -> int:
    """Append one novel attempt (grade + latency) to the sidecar; return its id.

    Append-only write to `gap.novel_revlog` — never touches `main.*`.
    """
    from gap import novel

    gapdb = open_for_collection(col)
    return novel.record_attempt(gapdb, item_id, correct, time_ms)


def list_concepts(col: Any) -> list[dict]:
    """`[{id, code, name}, ...]` for every concept, ordered by code.

    Backs the concept pickers in the novel dialog and menu.
    """
    gapdb = open_for_collection(col)
    rows = gapdb.all("SELECT id, code, name FROM gap.concepts ORDER BY code")
    return [{"id": int(cid), "code": code, "name": name} for cid, code, name in rows]


# --------------------------------------------------------------------------- #
# The one supported main-side mutation: suspend a retired concept's cards
# --------------------------------------------------------------------------- #
_CONCEPT_CARD_IDS_SQL = """
SELECT c.id
FROM main.cards c
JOIN main.notes n         ON n.id = c.nid
JOIN gap.note_concepts nc ON nc.guid = n.guid
WHERE nc.concept_id = ?
"""


def concept_card_ids(col: Any, concept_id: int) -> list[int]:
    """All card ids whose note's guid maps to `concept_id` in gap.note_concepts.

    Read-only: a plain join over `main.cards`/`main.notes`/`gap.note_concepts`.
    """
    gapdb = open_for_collection(col)
    return [int(cid) for cid in gapdb.list(_CONCEPT_CARD_IDS_SQL, concept_id)]


def suspend_concept_cards(col: Any, concept_id: int) -> int:
    """Suspend every card belonging to `concept_id`; return the count suspended.

    The gap->card mapping is read from `gap.note_concepts` (read-only on
    `main.*`). The only mutation is Anki's own supported suspend call
    (`col.sched.suspend_cards`), which handles undo, USN, and mod bumps — this
    layer never writes `main.cards` directly.
    """
    ids = concept_card_ids(col, concept_id)
    if not ids:
        return 0
    col.sched.suspend_cards(ids)
    return len(ids)
