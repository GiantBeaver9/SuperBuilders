"""SuperBuilders — the novel-item gate engine (pure Python, no GUI deps).

This package is the testable core of the Anki add-on: it opens the `gap.db`
sidecar, keeps the derived concept index in sync, assigns study arms, orders the
review queue by points-at-stake, records novel-item attempts, persists concept
retirement, and produces the dashboard payload. Every module runs the committed
SQL under `schema/` and `queries/` rather than reimplementing it, and works both
against Anki's `col.db` and a plain SQLite connection (see `gap.db`).
"""

from .db import (
    GapDB,
    SqliteProxy,
    open_sidecar,
    ensure_sidecar_schema,
    sidecar_path_for,
    attach_gap,
)

__all__ = [
    "GapDB",
    "SqliteProxy",
    "open_sidecar",
    "ensure_sidecar_schema",
    "sidecar_path_for",
    "attach_gap",
]
