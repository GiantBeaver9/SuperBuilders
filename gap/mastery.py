"""FSRS retrievability — computed in Python, on purpose.

Anki's bundled SQLite ships **no** math functions (`pow`/`exp`/`sqrt`/`ln` are
all missing — verified against anki 26.05). Retrievability needs a real power,
so the live engine cannot compute it in SQL the way the offline analysis queries
do. This module is that one piece of math: SQL fetches raw card state (math-free,
Anki-safe), Python turns it into a retrievability in [0, 1].

The formula is FSRS-5, identical to the one the analysis SQL uses:

    R(t) = (1 + FACTOR * t / S) ** DECAY,   DECAY = -0.5,  FACTOR = 19/81

`tests/` pins this Python result equal to the SQL result on shared seeded data,
so the live path and the analysis path can never silently drift.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

DECAY: float = -0.5
FACTOR: float = 19.0 / 81.0
DAY_MS: int = 86_400_000


def now_ms() -> int:
    """Epoch milliseconds — the unit of Anki ids/revlog and of gap.* timestamps."""
    return int(time.time() * 1000)


def retrievability(stability: float, elapsed_days: float,
                   decay: float = DECAY, factor: float = FACTOR) -> float:
    """FSRS-5 retrievability. `stability` in days (S), `elapsed_days` since the
    last review (t). Returns a probability in (0, 1]. Elapsed is clamped at 0 so a
    just-reviewed card reads R = 1, never > 1."""
    if stability is None or stability <= 0:
        return 0.0
    t = max(0.0, elapsed_days)
    return (1.0 + factor * t / stability) ** decay


def fsrs_stability(card_data: str | None) -> float | None:
    """Pull FSRS stability out of `cards.data` JSON (`$.s`; keys per Anki's
    `CardData` — s/d/dr/decay/pos/cd). Parsed in Python so an empty string or
    non-JSON blob (Anki's default for a card with no FSRS state) yields None
    instead of raising — SQL `json_extract` would error on `''`."""
    if not card_data:
        return None
    try:
        obj = json.loads(card_data)
    except (ValueError, TypeError):
        return None
    s = obj.get("s") if isinstance(obj, dict) else None
    return float(s) if isinstance(s, (int, float)) else None


# --- SQL that only uses functions Anki's SQLite actually has ---------------- #
# json is parsed in Python (above), so this fetch is pure column reads + a
# scalar subquery for the last review. No math, no json_extract.
_CARD_STATE_SQL = """
SELECT
  c.id          AS card_id,
  nc.concept_id AS concept_id,
  c.data        AS data,
  (SELECT MAX(r.id) FROM main.revlog r WHERE r.cid = c.id) AS last_review_ms
FROM main.cards c
JOIN main.notes n         ON n.id = c.nid
JOIN gap.note_concepts nc ON nc.guid = n.guid
"""


def card_retrievabilities(gapdb: Any, at_ms: int | None = None
                          ) -> list[tuple[int, int, float]]:
    """(card_id, concept_id, R) for every concept-linked card that carries an
    FSRS stability and has been reviewed. Cards with no state or no review are
    omitted (they contribute no R), matching CONVENTIONS 'card_mastery'."""
    at = now_ms() if at_ms is None else at_ms
    out: list[tuple[int, int, float]] = []
    for card_id, concept_id, data, last_review_ms in gapdb.all(_CARD_STATE_SQL):
        stability = fsrs_stability(data)
        if stability is None or last_review_ms is None:
            continue
        elapsed_days = (at - last_review_ms) / DAY_MS
        out.append((card_id, concept_id, retrievability(stability, elapsed_days)))
    return out


def card_mastery_by_concept(gapdb: Any, at_ms: int | None = None
                            ) -> dict[int, float]:
    """concept_id -> mean card retrievability (the pre-registration's
    `card_mastery`). Concepts whose cards all lack state are absent from the map."""
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for _card_id, concept_id, r in card_retrievabilities(gapdb, at_ms):
        sums[concept_id] = sums.get(concept_id, 0.0) + r
        counts[concept_id] = counts.get(concept_id, 0) + 1
    return {cid: sums[cid] / counts[cid] for cid in sums}


# --- novel accuracy (pure SQL, no math — safe live and offline) ------------- #
def novel_accuracy_by_concept(gapdb: Any, holdout: bool = False
                              ) -> dict[int, float]:
    """concept_id -> mean novel `correct`. `holdout=False` is practice accuracy
    (drives the gate and the queue); `holdout=True` is the held-out terminal set.
    Kept strictly separate per CONVENTIONS."""
    rows = gapdb.all(
        """
        SELECT nic.concept_id, AVG(nr.correct) AS acc
        FROM gap.novel_revlog nr
        JOIN gap.novel_items ni          ON ni.id = nr.item_id
        JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
        WHERE ni.is_holdout = ?
        GROUP BY nic.concept_id
        """,
        1 if holdout else 0,
    )
    return {cid: float(acc) for cid, acc in rows if acc is not None}
