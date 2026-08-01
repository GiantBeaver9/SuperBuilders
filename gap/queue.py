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
# DUE PREDICATE. A candidate is a review or day-learning card — `queue IN (2, 3)`.
# For those queues Anki stores `cards.due` as a DAY NUMBER (days since collection
# creation), and a card is due when `due <= today`, where `today` is the
# collection's current day number (`col.sched.today`).
#   * Live add-on: `service` passes `today`, so the queue filters genuinely-due
#     cards with correct day-number semantics.
#   * Offline (sim / tests, `open_sidecar`): there is no collection day cutoff, so
#     `today` is None and we rank ALL review/day-learning cards for the concept —
#     stated honestly rather than hidden behind an always-true `due <= now_ms`
#     comparison (day-number `due` vs an epoch-ms "now" never filters anything).
# Both variants use only column reads, `IN`, and `<=` — no math function — so the
# query runs live inside Anki. The `?` bind is Python-side (the no-bind rule
# governs the committed .sql files only).
# --------------------------------------------------------------------------- #
_DUE_CARDS_BASE = """
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
"""
_DUE_TODAY_CLAUSE = "  AND c.due <= ?\n"


def points_at_stake(gapdb: Any, at_ms: int | None = None,
                    today: int | None = None) -> list[dict]:
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
    :param today: the collection's current day number (``col.sched.today``). When
        given, only cards with ``due <= today`` are returned (genuinely due). When
        ``None`` (offline), every review/day-learning card is ranked.
    """
    at = mastery.now_ms() if at_ms is None else at_ms

    card_mastery = mastery.card_mastery_by_concept(gapdb, at)         # concept -> mean R
    novel_practice = mastery.novel_accuracy_by_concept(gapdb, holdout=False)

    if today is None:
        rows = gapdb.all(_DUE_CARDS_BASE)
    else:
        rows = gapdb.all(_DUE_CARDS_BASE + _DUE_TODAY_CLAUSE, today)

    out: list[dict] = []
    for card_id, concept_id, code, weight in rows:
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


def ranked_card_ids(gapdb: Any, at_ms: int | None = None,
                    today: int | None = None) -> list[int]:
    """The due card ids in points-at-stake order (largest gap first).

    Thin projection of :func:`points_at_stake` down to just ``card_id``.

    :param gapdb: a ``GapDB`` (Anki ``col.db`` or ``open_sidecar`` proxy).
    :param at_ms: evaluation instant in epoch ms; ``None`` means "now".
    :param today: the collection's current day number (``col.sched.today``); see
        :func:`points_at_stake`.
    """
    return [row["card_id"] for row in points_at_stake(gapdb, at_ms, today)]
