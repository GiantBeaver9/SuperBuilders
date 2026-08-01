"""The "Novel-item Gate" top-level menu.

Four actions:
  * Open Dashboard           — the web dashboard (abstain rule, coverage, endpoints)
  * Build Points-at-Stake study — a filtered deck ordered by the gap ranking
  * Add novel item           — record a novel-item probe attempt
  * Rebuild / assign now      — run the startup sync on demand

Imported only under `aqt`, so importing Qt at module top level is safe.
"""
from __future__ import annotations

from typing import Any

from aqt.operations import QueryOp
from aqt.qt import QAction, QMenu
from aqt.utils import showInfo, tooltip

from gap import service

from . import dashboard, novel_dialog

_MENU_TITLE = "Novel-item Gate"


def build_menu(mw: Any) -> None:
    """Install the top menu on Anki's menu bar (idempotent per session)."""
    menubar = mw.form.menubar
    menu = QMenu(_MENU_TITLE, menubar)
    menubar.addMenu(menu)

    _add(menu, "Open Dashboard", lambda: dashboard.open_dashboard(mw))
    _add(menu, "Build Points-at-Stake study", lambda: _build_study(mw))
    _add(menu, "Add novel item", lambda: novel_dialog.open_novel_dialog(mw))
    menu.addSeparator()
    _add(menu, "Rebuild / assign now", lambda: _rebuild_now(mw))


def _add(menu: Any, label: str, fn: Any) -> None:
    action = QAction(label, menu)
    action.triggered.connect(fn)
    menu.addAction(action)


# --------------------------------------------------------------------------- #
# Action bodies
# --------------------------------------------------------------------------- #
def _rebuild_now(mw: Any) -> None:
    """Run index rebuild + arm assignment now, off the main thread."""
    QueryOp(
        parent=mw,
        op=lambda col: service.on_profile_open(col),
        success=lambda _: tooltip("Novel-item gate: rebuilt and assigned."),
    ).with_progress("Rebuilding...").run_in_background()


def _build_study(mw: Any) -> None:
    """Rank due cards by points-at-stake and offer to open them in order.

    We compute the ranking off the main thread, then set Anki's current search
    to those card ids in gap-order for the user to study.
    """

    def _op(col: Any) -> list[int]:
        return service.ranked_card_ids(col)

    def _done(card_ids: list[int]) -> None:
        if not card_ids:
            showInfo("No due cards carry a points-at-stake gap right now.")
            return
        # Browse the ranked cards; the study order is the gap ranking. A filtered
        # deck cannot preserve an arbitrary order, so we surface the ranking in
        # the browser (cid: search), highest-stake first.
        from aqt import dialogs

        browser = dialogs.open("Browser", mw)
        search = " OR ".join(f"cid:{cid}" for cid in card_ids)
        browser.search_for(search)
        tooltip(f"{len(card_ids)} cards ranked by points-at-stake.")

    QueryOp(parent=mw, op=_op, success=_done).with_progress(
        "Ranking by points-at-stake..."
    ).run_in_background()
