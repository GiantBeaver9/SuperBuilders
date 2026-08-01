"""Novel-item probe dialog.

A *novel item* is a transfer probe the student has never seen — not an Anki card
(it never enters `main.revlog`). This dialog picks the next practice item for a
chosen concept, times the student's response, and records a self-graded
correct/incorrect plus latency into the sidecar via the engine facade.

The item's *content* is not stored in `gap.db` (the schema keeps only a guid and
a `source_id` pointing at the external passage bank), so the dialog presents the
item's identifiers and lets the student self-report — the pre-registration's
novel probe is a grade+latency signal, not a rendered card. Imported only under
`aqt`, so importing Qt at module top level is safe.
"""
from __future__ import annotations

import time
from typing import Any

from aqt.qt import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    Qt,
)
from aqt.utils import tooltip

from gap import service


class NovelItemDialog(QDialog):
    """Present one concept's next novel item; capture correct/incorrect + latency."""

    def __init__(self, mw: Any, parent: Any = None) -> None:
        super().__init__(parent or mw)
        self.mw = mw
        self.col = mw.col
        self._concepts = service.list_concepts(self.col)
        self._item_id: int | None = None
        self._started_ms: float | None = None

        self.setWindowTitle("Novel-item Probe")
        self.setMinimumWidth(420)
        self._build()
        if self._concepts:
            self._load_next_item()
        else:
            self.prompt.setText("No concepts found. Tag notes with concept::<code> first.")
            self._set_grading_enabled(False)

    # -- construction ------------------------------------------------------- #
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Concept:"))
        self.concept_box = QComboBox()
        for c in self._concepts:
            self.concept_box.addItem(f"{c['code']} — {c['name']}", c["id"])
        self.concept_box.currentIndexChanged.connect(self._on_concept_changed)
        row.addWidget(self.concept_box, 1)
        layout.addLayout(row)

        self.prompt = QLabel()
        self.prompt.setWordWrap(True)
        self.prompt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.prompt.setMinimumHeight(80)
        layout.addWidget(self.prompt)

        grade_row = QHBoxLayout()
        self.correct_btn = QPushButton("Correct")
        self.incorrect_btn = QPushButton("Incorrect")
        self.correct_btn.clicked.connect(lambda: self._record(True))
        self.incorrect_btn.clicked.connect(lambda: self._record(False))
        grade_row.addWidget(self.incorrect_btn)
        grade_row.addWidget(self.correct_btn)
        layout.addLayout(grade_row)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    # -- item flow ---------------------------------------------------------- #
    def _current_concept_id(self) -> int | None:
        data = self.concept_box.currentData()
        return int(data) if data is not None else None

    def _on_concept_changed(self, _index: int) -> None:
        self._load_next_item()

    def _load_next_item(self) -> None:
        concept_id = self._current_concept_id()
        if concept_id is None:
            return
        self._item_id = service.next_novel_item(self.col, concept_id, holdout=False)
        if self._item_id is None:
            self.prompt.setText("No practice novel items available for this concept.")
            self._set_grading_enabled(False)
            self._started_ms = None
            return
        self.prompt.setText(
            f"Novel item #{self._item_id} is ready.\n\n"
            "Attempt it against the external passage, then self-grade below."
        )
        self._set_grading_enabled(True)
        self._started_ms = time.monotonic() * 1000.0

    def _set_grading_enabled(self, enabled: bool) -> None:
        self.correct_btn.setEnabled(enabled)
        self.incorrect_btn.setEnabled(enabled)

    def _record(self, correct: bool) -> None:
        if self._item_id is None:
            return
        if self._started_ms is None:
            latency_ms = 0
        else:
            latency_ms = int(max(0.0, time.monotonic() * 1000.0 - self._started_ms))
        service.record_novel_attempt(self.col, self._item_id, correct, latency_ms)
        tooltip(f"Recorded {'correct' if correct else 'incorrect'} ({latency_ms} ms).")
        self._load_next_item()


def open_novel_dialog(mw: Any) -> None:
    """Menu entry point: build and show the novel-item dialog."""
    NovelItemDialog(mw, parent=mw).show()
