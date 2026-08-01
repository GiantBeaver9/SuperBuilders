"""Retirement evaluation — the app's ground-truth "this concept is done" signal.

Retirement is persisted, not derived: a concept is retired **iff** it has a row in
`gap.retirements` (presence, not a boolean — see queries/CONVENTIONS.md and
schema/gap.sql). This module tests each concept's arm-specific rule and, when the
rule fires, writes that one row (append-only; retirement does not un-happen).

Per-arm rules (arm -> trigger):

  * ``gate``    -> ``novel_gate``   : PRACTICE novel accuracy (``is_holdout = 0``)
                                      >= 0.7.
  * ``nogate``  -> ``card_mastery`` : the concept has >= 1 reviewed card AND every
                                      reviewed card has FSRS retrievability
                                      R >= 0.9 (computed in Python via
                                      ``gap.mastery``). "Reviewed card" == a card
                                      that carries FSRS memory state and has at
                                      least one review, i.e. one that
                                      ``gap.mastery.card_retrievabilities`` yields
                                      an R for.
  * ``vanilla`` -> ``anki_default`` : Anki-maturity proxy. Unmodified Anki knows
                                      nothing of gap.db, so the sidecar OBSERVES
                                      Anki's own state instead of writing main.*.
                                      The proxy for "Anki graduated / matured this
                                      concept" is: the concept has >= 1 card AND
                                      every card is MATURE, where mature ==
                                      ``cards.ivl >= 21`` days (Anki's own
                                      young/mature boundary). Read with math-free
                                      SQL (COUNT + a CASE SUM), Anki-safe.

Only the ``card_mastery`` (nogate) rule needs Python — retrievability has a
`pow`, which Anki's SQLite lacks. The gate and vanilla rules are pure SQL that
Anki can run live. ``retired_ms`` is always required to be >= the concept's
``arms.assigned_ms`` (retirement postdates assignment); a concept whose evaluation
instant precedes its assignment is skipped, never retired.
"""
from __future__ import annotations

from typing import Any

from gap import mastery

# Practice-novel-accuracy retirement threshold for the gate arm (pre-registration).
GATE_THRESHOLD: float = 0.7
# Retrievability every reviewed card must clear for the nogate arm.
MASTERY_THRESHOLD: float = 0.9
# Anki's young/mature interval boundary (days) for the vanilla arm's proxy.
MATURE_IVL_DAYS: int = 21

_ARM_TRIGGER: dict[str, str] = {
    "gate": "novel_gate",
    "nogate": "card_mastery",
    "vanilla": "anki_default",
}

# Math-free maturity proxy for the vanilla arm: total cards of the concept and how
# many are mature (ivl >= 21). Only column reads, COUNT, CASE and SUM — Anki-safe.
_VANILLA_MATURITY_SQL = """
SELECT
  COUNT(*)                                        AS total,
  SUM(CASE WHEN c.ivl >= ? THEN 1 ELSE 0 END)     AS mature
FROM main.cards c
JOIN main.notes n         ON n.id   = c.nid
JOIN gap.note_concepts nc ON nc.guid = n.guid
WHERE nc.concept_id = ?
"""


def _reviewed_R_by_concept(gapdb: Any, at_ms: int) -> dict[int, list[float]]:
    """concept_id -> list of retrievabilities, one per reviewed card (via mastery)."""
    out: dict[int, list[float]] = {}
    for _card_id, concept_id, r in mastery.card_retrievabilities(gapdb, at_ms):
        out.setdefault(concept_id, []).append(r)
    return out


def _rule_fires(gapdb: Any, concept_id: int, arm: str, at_ms: int,
                novel_practice: dict[int, float],
                reviewed_R: dict[int, list[float]]) -> bool:
    """Whether ``arm``'s retirement rule is met for ``concept_id`` as of ``at_ms``."""
    if arm == "gate":
        acc = novel_practice.get(concept_id)
        return acc is not None and acc >= GATE_THRESHOLD

    if arm == "nogate":
        rs = reviewed_R.get(concept_id)
        return bool(rs) and all(r >= MASTERY_THRESHOLD for r in rs)

    if arm == "vanilla":
        total, mature = gapdb.all(_VANILLA_MATURITY_SQL, MATURE_IVL_DAYS, concept_id)[0]
        return total is not None and total >= 1 and (mature or 0) == total

    return False  # unknown arm never retires


def evaluate(gapdb: Any, at_ms: int | None = None) -> list[dict]:
    """Test every not-yet-retired arm's rule and persist the ones that fire.

    For each concept in ``gap.arms`` with no ``gap.retirements`` row, evaluates its
    arm's rule (see module docstring). When the rule is met and ``at_ms`` is >= the
    concept's ``arms.assigned_ms``, inserts a ``gap.retirements`` row
    (``retired_ms = at_ms``, ``trigger`` per arm) and includes it in the returned
    list. Idempotent: a concept already in ``gap.retirements`` is never re-tested,
    so a second call retires nothing new.

    :param gapdb: a ``GapDB`` (Anki ``col.db`` or ``open_sidecar`` proxy).
    :param at_ms: evaluation instant / ``retired_ms`` in epoch ms; ``None`` = now.
    :returns: newly-retired concepts as ``[{concept_id, arm, trigger}, ...]``.
    """
    at = mastery.now_ms() if at_ms is None else at_ms

    pending = gapdb.all(
        """
        SELECT a.concept_id, a.arm, a.assigned_ms
        FROM gap.arms a
        WHERE a.concept_id NOT IN (SELECT r.concept_id FROM gap.retirements r)
        """
    )

    # Precompute the two Python-side inputs once for the whole batch.
    novel_practice = mastery.novel_accuracy_by_concept(gapdb, holdout=False)
    reviewed_R = _reviewed_R_by_concept(gapdb, at)

    newly: list[dict] = []
    for concept_id, arm, assigned_ms in pending:
        if not _rule_fires(gapdb, concept_id, arm, at, novel_practice, reviewed_R):
            continue
        # retired_ms must postdate (>=) assignment; skip an out-of-order instant.
        if at < assigned_ms:
            continue
        trigger = _ARM_TRIGGER[arm]
        gapdb.execute(
            "INSERT INTO gap.retirements (concept_id, retired_ms, trigger) "
            "VALUES (?, ?, ?)",
            concept_id, at, trigger,
        )
        newly.append({"concept_id": concept_id, "arm": arm, "trigger": trigger})

    gapdb.commit()
    return newly


def is_retired(gapdb: Any, concept_id: int) -> bool:
    """True iff ``concept_id`` has a ``gap.retirements`` row (retirement is presence)."""
    return gapdb.scalar(
        "SELECT 1 FROM gap.retirements WHERE concept_id = ?", concept_id
    ) is not None


def retired_concepts(gapdb: Any) -> dict[int, str]:
    """Map of every retired ``concept_id`` -> its retirement ``trigger``."""
    return {
        cid: trigger
        for cid, trigger in gapdb.all(
            "SELECT concept_id, trigger FROM gap.retirements"
        )
    }
