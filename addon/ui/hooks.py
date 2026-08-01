"""Hook registration and reviewer answer timing.

This module is imported only when `aqt` is present (the package `__init__`
guards the import), so it may import `aqt`/Qt at module top level.

Two responsibilities:

* **Reviewer latency.** Anki's own `revlog.time` already records answer latency,
  but the pre-registration wants an explicit, independent show->answer timer we
  control. We stamp a monotonic show time on `reviewer_did_show_question` and
  read it on `reviewer_did_answer_card`, passing the elapsed milliseconds into
  `service.on_review_answered`.
* **Retirement reaction.** After Anki logs the answer, we re-evaluate retirement
  in the background; any newly-retired concept has its cards suspended (through
  Anki's supported scheduler call) and the user is toasted.
"""
from __future__ import annotations

import time
from typing import Any

from aqt import gui_hooks, mw
from aqt.operations import CollectionOp, QueryOp
from aqt.utils import tooltip

from gap import service

# Monotonic timestamp (ms) of the last shown question. One reviewer is active at
# a time, so a single module-level slot is enough. `None` means no pending show.
_shown_at_ms: float | None = None


def _now_ms() -> float:
    return time.monotonic() * 1000.0


def on_show_question(card: Any) -> None:
    """Record when the current question became visible (answer latency start)."""
    global _shown_at_ms
    _shown_at_ms = _now_ms()


def on_answer_card(reviewer: Any, card: Any, ease: int) -> None:
    """Anki has just logged the review — compute latency, re-check retirement.

    Runs the retirement re-evaluation off the main thread (`QueryOp`); on
    success, suspends each newly-retired concept's cards and toasts the user.
    """
    global _shown_at_ms
    if _shown_at_ms is None:
        latency_ms = int(card.time_taken()) if hasattr(card, "time_taken") else 0
    else:
        latency_ms = int(max(0.0, _now_ms() - _shown_at_ms))
    _shown_at_ms = None

    card_id = int(card.id)

    def _op(col: Any) -> tuple[int, list[int]]:
        # Both steps are read-only: evaluate retirement, then gather the card ids
        # of every newly-retired concept. The mutation (suspend) is deferred to a
        # CollectionOp on the main thread so undo + UI refresh are handled.
        retired = service.on_review_answered(col, card_id, int(ease), latency_ms)
        concept_ids = [int(r["concept_id"]) for r in retired if "concept_id" in r]
        card_ids: list[int] = []
        for cid in concept_ids:
            card_ids.extend(service.concept_card_ids(col, cid))
        card_ids = list(dict.fromkeys(card_ids))  # a note can hit several concepts
        return len(concept_ids), card_ids

    def _done(result: tuple[int, list[int]]) -> None:
        _suspend_retired(*result)

    QueryOp(parent=mw, op=_op, success=_done).run_in_background()


def _suspend_retired(n_concepts: int, card_ids: list[int]) -> None:
    """Suspend the gathered cards via Anki's supported call, then toast."""
    if n_concepts == 0:
        return
    noun = "concept" if n_concepts == 1 else "concepts"
    if not card_ids:
        tooltip(f"Retired {n_concepts} {noun}.")
        return

    CollectionOp(
        parent=mw, op=lambda col: col.sched.suspend_cards(card_ids)
    ).success(
        lambda _out: tooltip(
            f"Retired {n_concepts} {noun}; suspended {len(card_ids)} card(s)."
        )
    ).run_in_background()


def on_profile_open() -> None:
    """Startup sync in the background: index rebuild + arm assignment."""
    QueryOp(
        parent=mw,
        op=lambda col: service.on_profile_open(col),
        success=lambda _: None,
    ).with_progress("Syncing novel-item gate...").run_in_background()


def register() -> None:
    """Wire every gui_hook. Called once from the package `__init__`."""
    gui_hooks.profile_did_open.append(on_profile_open)
    gui_hooks.reviewer_did_show_question.append(on_show_question)
    gui_hooks.reviewer_did_answer_card.append(on_answer_card)
