"""Points-at-stake study queue — the live ranking of due cards.

This is the Python twin of `queries/03_queue/points_at_stake.sql`. That SQL is the
canonical OFFLINE ranking spec, but it computes retrievability with `pow(...)`,
which Anki's bundled SQLite does not have (see docs/ENGINE.md §2). So the live
path here keeps SQL strictly math-free — it only fetches card→concept→weight rows
and novel accuracy — and does the one piece of math (FSRS retrievability, hence
`card_mastery`) in Python via `gap.mastery`. The two paths are pinned equal by a
parity test (`tests/test_queue_retirement.py`).

Ranking key (from the pre-registration):

    points = (card_mastery - novel_accuracy) * weight

Largest gap first. `card_mastery` is the concept's mean FSRS retrievability;
`novel_accuracy` is PRACTICE novel accuracy (`is_holdout = 0`), COALESCEd to 0.0
for concepts with no novel attempts yet so brand-new concepts still surface;
`weight` is `gap.concepts.weight` (exam weight).
"""
from __future__ import annotations

from typing import Any

from gap import mastery

# --------------------------------------------------------------------------- #
# Math-free "due card" fetch (Anki-SQLite-safe).
#
# DUE PREDICATE (mirrors points_at_stake.sql's documented portable proxy):
#   a card is due when it is a review or day-learning card — `queue IN (2, 3)` —
#   whose `due` value is at or before "now" (`due <= at_ms`). This is the same
#   simplification the canonical SQL makes: Anki's real `cards.due` units differ
#   by queue (review / day-learning is a day number since collection creation;
#   intraday learning is epoch seconds), so comparing `due` to an epoch-ms "now"
#   is a deliberately portable proxy — "due today-ish" — not a faithful
#   reproduction of Anki's per-queue due arithmetic. It uses only column reads,
#   `IN`, and `<=`: no math function, so it runs live inside Anki. The `?` bind is
#   Python-side (the no-bind-params rule governs the committed .sql files only).
# --------------------------------------------------------------------------- #
_DUE_CARDS_SQL = """
SELECT
  c.id          AS card_id,
  nc.concept_id AS concept_id,
  con.code      AS code,
  con.weight    AS weight
FROM main.cards c
JOIN main.notes n         ON n.id   = c.nid
JOIN gap.note_concepts nc ON nc.guid = n.guid
JOIN gap.concepts con     ON con.id  = nc.concept_id
WHERE c.queue IN (2, 3)
  AND c.due <= ?
"""


def points_at_stake(gapdb: Any, at_ms: int | None = None) -> list[dict]:
    """Rank every DUE card by the points-at-stake key, largest gap first.

    Returns one dict per due card:

        {card_id, concept_id, code, card_mastery, novel_accuracy, weight, points}

    where ``points = (card_mastery - novel_accuracy) * weight``. ``card_mastery``
    is the concept's mean FSRS retrievability (Python, via ``gap.mastery``);
    ``novel_accuracy`` is PRACTICE novel accuracy (``is_holdout = 0``) COALESCEd to
    0.0 when the concept has no practice attempts yet; ``weight`` is
    ``gap.concepts.weight``. A concept whose cards carry no FSRS memory state
    contributes ``card_mastery = 0.0`` (mirroring the SQL's
    ``COALESCE(card_mastery, 0.0)`` in the ranking expression).

    All SQL issued here is math-free, so it runs live inside Anki. The result is
    ordered in Python (``points`` descending); ties fall back to ``card_id`` for a
    stable, deterministic order.

    :param gapdb: a ``GapDB`` (Anki ``col.db`` or ``open_sidecar`` proxy).
    :param at_ms: evaluation instant in epoch ms; ``None`` means "now".
    """
    at = mastery.now_ms() if at_ms is None else at_ms

    card_mastery = mastery.card_mastery_by_concept(gapdb, at)         # concept -> mean R
    novel_practice = mastery.novel_accuracy_by_concept(gapdb, holdout=False)

    out: list[dict] = []
    for card_id, concept_id, code, weight in gapdb.all(_DUE_CARDS_SQL, at):
        cm = card_mastery.get(concept_id, 0.0)          # COALESCE missing mastery -> 0.0
        na = novel_practice.get(concept_id, 0.0)        # COALESCE missing novel   -> 0.0
        w = float(weight)
        out.append(
            {
                "card_id": card_id,
                "concept_id": concept_id,
                "code": code,
                "card_mastery": cm,
                "novel_accuracy": na,
                "weight": w,
                "points": (cm - na) * w,
            }
        )

    # Rank by the points-at-stake key DESC; card_id tiebreak keeps it deterministic.
    out.sort(key=lambda d: (-d["points"], d["card_id"]))
    return out


def ranked_card_ids(gapdb: Any, at_ms: int | None = None) -> list[int]:
    """The due card ids in points-at-stake order (largest gap first).

    Thin projection of :func:`points_at_stake` down to just ``card_id``.

    :param gapdb: a ``GapDB`` (Anki ``col.db`` or ``open_sidecar`` proxy).
    :param at_ms: evaluation instant in epoch ms; ``None`` means "now".
    """
    return [row["card_id"] for row in points_at_stake(gapdb, at_ms)]
