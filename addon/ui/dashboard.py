"""Dashboard dialog — hosts the web dashboard and feeds it the engine payload.

A `QWebEngineView` loads `addon/ui/web/dashboard.html` from a real local file
URL (so its relative `dashboard.css` / `dashboard.js` resolve), and the engine's
`service.dashboard_payload(mw.col)` is injected as `window.DASHBOARD_DATA`.

The injection uses a `QWebEngineScript` at `DocumentCreation`, which runs
*before* the page's own scripts — so `dashboard.js` can read
`window.DASHBOARD_DATA` synchronously at load, with no ready-race. The
`.html`/`.css`/`.js` are authored separately; this module only hosts them.
Imported only under `aqt`, so importing Qt at module top level is safe.
"""
from __future__ import annotations

import json
import os
from typing import Any

from aqt.qt import (
    QDialog,
    QLabel,
    QUrl,
    QVBoxLayout,
    QWebEngineScript,
    QWebEngineView,
)

from gap import service

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_HTML_PATH = os.path.join(_WEB_DIR, "dashboard.html")


class DashboardDialog(QDialog):
    """Modeless dialog rendering the novel-item-gate dashboard."""

    def __init__(self, mw: Any, parent: Any = None) -> None:
        super().__init__(parent or mw)
        self.mw = mw
        self.setWindowTitle("Novel-item Gate — Dashboard")
        self.resize(960, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not os.path.isfile(_HTML_PATH):
            layout.addWidget(QLabel(f"dashboard.html not found at {_HTML_PATH}"))
            return

        payload = service.dashboard_payload(mw.col)
        self.web = QWebEngineView(self)
        layout.addWidget(self.web)
        self._inject_data(payload)
        self.web.load(QUrl.fromLocalFile(_HTML_PATH))

    def _inject_data(self, payload: dict) -> None:
        """Set `window.DASHBOARD_DATA` at document creation, before page scripts."""
        source = "window.DASHBOARD_DATA = %s;" % json.dumps(payload)
        script = QWebEngineScript()
        script.setName("dashboard-data")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(source)
        self.web.page().scripts().insert(script)


def open_dashboard(mw: Any) -> None:
    """Menu entry point: build and show the dashboard dialog."""
    DashboardDialog(mw, parent=mw).show()
