"""Sidecar database access — the shared foundation the whole engine sits on.

Two things this module guarantees so every other `gap.*` module can stay simple:

1. **One DB interface, two backends.** The engine only ever calls `.execute`,
   `.all`, `.scalar`, `.list` on a proxy object. Inside a real add-on that proxy
   is Anki's own `col.db` (`anki.dbproxy.DBProxy`); in the simulation and tests it
   is `SqliteProxy`, a thin wrapper over `sqlite3.Connection` that mirrors the
   same method surface. Nothing downstream knows or cares which it is talking to.

2. **The committed SQL is the source of truth.** `schema/gap.sql` and the ten
   files under `queries/` are loaded and run verbatim — the engine never inlines a
   second copy of that logic. `run_file` splits a multi-statement file and runs
   each part; `query`/`query_all` return rows.

The sidecar lives beside `collection.anki2` (so it survives an AnkiWeb download
that replaces the collection wholesale) and is ATTACHed as `gap`. This module
never writes `main.*`.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------- #
# Locating the canonical SQL (schema/gap.sql + queries/**.sql)
# --------------------------------------------------------------------------- #
def _resolve_sql_root(explicit: str | os.PathLike | None = None) -> Path:
    """Directory that contains `schema/gap.sql` and `queries/`.

    Order: explicit arg → $GAP_SQL_ROOT → a bundled `sql/` dir next to this
    package (populated by scripts/bundle_addon.py) → walk up from here until a
    dir with `schema/gap.sql` is found (the repo root during dev/tests).
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("GAP_SQL_ROOT"):
        candidates.append(Path(os.environ["GAP_SQL_ROOT"]))
    here = Path(__file__).resolve()
    candidates.append(here.parent / "sql")          # bundled copy inside the add-on
    for parent in here.parents:                      # dev: repo root
        candidates.append(parent)
    for c in candidates:
        if (c / "schema" / "gap.sql").is_file():
            return c
    raise FileNotFoundError(
        "could not locate schema/gap.sql; set GAP_SQL_ROOT or pass sql_root="
    )


def split_statements(sql: str) -> list[str]:
    """Split a .sql file into executable statements, stripping `--` comments.

    A `;` only ends a statement when it is outside a single-quoted string and
    outside a comment — the naive line-split breaks on the `;` that appears
    *inside* an inline comment (e.g. `-- survives export/import; nid does not`).
    This walks the text tracking string state (`''` escape included) and drops
    `--`-to-end-of-line comments, so comment/​string punctuation can never split a
    statement. Empty fragments are dropped.
    """
    out: list[str] = []
    cur: list[str] = []
    i, n, in_str = 0, len(sql), False
    while i < n:
        ch = sql[i]
        if in_str:
            cur.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":   # '' escape inside a string
                    cur.append(sql[i + 1]); i += 2; continue
                in_str = False
            i += 1
        elif ch == "'":
            in_str = True; cur.append(ch); i += 1
        elif ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)                     # comment: skip to end of line
            i = n if j == -1 else j
        elif ch == ";":
            stmt = "".join(cur).strip()
            if stmt:
                out.append(stmt)
            cur = []; i += 1
        else:
            cur.append(ch); i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


# --------------------------------------------------------------------------- #
# DBProxy-compatible wrapper for the non-Anki (sim / test) path
# --------------------------------------------------------------------------- #
class SqliteProxy:
    """Mirror the subset of `anki.dbproxy.DBProxy` the engine uses, over a plain
    `sqlite3.Connection`. Method names and return shapes match Anki's proxy so
    engine code runs identically on `col.db` and on this."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def execute(self, sql: str, *args: Any) -> list[tuple]:
        return self.con.execute(sql, args).fetchall()

    def all(self, sql: str, *args: Any) -> list[tuple]:
        return self.con.execute(sql, args).fetchall()

    def first(self, sql: str, *args: Any) -> tuple | None:
        return self.con.execute(sql, args).fetchone()

    def scalar(self, sql: str, *args: Any) -> Any:
        row = self.con.execute(sql, args).fetchone()
        return row[0] if row else None

    def list(self, sql: str, *args: Any) -> list[Any]:
        return [r[0] for r in self.con.execute(sql, args).fetchall()]

    def commit(self) -> None:
        self.con.commit()


# --------------------------------------------------------------------------- #
# The sidecar handle
# --------------------------------------------------------------------------- #
class GapDB:
    """Runs the committed schema and queries against whichever DB proxy it is
    given. Construct with Anki's `col.db` in the add-on, or with `open_sidecar`
    in the sim/tests."""

    def __init__(self, db: Any, sql_root: str | os.PathLike | None = None):
        self.db = db
        self.sql_root = _resolve_sql_root(sql_root)

    # -- SQL file access ---------------------------------------------------- #
    def sql_text(self, relpath: str) -> str:
        return (self.sql_root / relpath).read_text()

    def run_file(self, relpath: str, params: Sequence[Any] = ()) -> list[tuple]:
        """Execute every statement in a file; return the rows of the last one.
        `params` binds only the final statement (the others take none)."""
        stmts = split_statements(self.sql_text(relpath))
        rows: list[tuple] = []
        for i, st in enumerate(stmts):
            if i == len(stmts) - 1 and params:
                rows = self.db.execute(st, *params)
            else:
                rows = self.db.execute(st)
        return rows

    def query(self, relpath: str, params: Sequence[Any] = ()) -> list[tuple]:
        """A file expected to hold a single SELECT: return its rows."""
        return self.run_file(relpath, params)

    def query_all(self, relpath: str) -> list[list[tuple]]:
        """A file with several SELECTs (e.g. primary_crossover): return one row
        list per statement, in order."""
        return [self.db.execute(st) for st in split_statements(self.sql_text(relpath))]

    # -- schema ------------------------------------------------------------- #
    def apply_schema(self) -> None:
        """Create/upgrade the sidecar tables. Idempotent (CREATE IF NOT EXISTS)."""
        for st in split_statements(self.sql_text("schema/gap.sql")):
            self.db.execute(st)
        if hasattr(self.db, "commit"):
            self.db.commit()

    # -- low-level passthroughs -------------------------------------------- #
    def all(self, sql: str, *args: Any) -> list[tuple]:
        return self.db.all(sql, *args)

    def scalar(self, sql: str, *args: Any) -> Any:
        return self.db.scalar(sql, *args)

    def list(self, sql: str, *args: Any) -> list[Any]:
        return self.db.list(sql, *args)

    def execute(self, sql: str, *args: Any) -> list[tuple]:
        return self.db.execute(sql, *args)

    def commit(self) -> None:
        if hasattr(self.db, "commit"):
            self.db.commit()


# --------------------------------------------------------------------------- #
# Sidecar path + opener (sim / test path; the add-on uses col.db + attach_gap)
# --------------------------------------------------------------------------- #
def sidecar_path_for(collection_path: str | os.PathLike) -> Path:
    """gap.db lives beside collection.anki2, same stem + `.gap.db`."""
    p = Path(collection_path)
    return p.with_name("gap.db")


def attach_gap(db: Any, gap_path: str | os.PathLike, alias: str = "gap") -> None:
    """ATTACH the sidecar onto an existing connection (real add-on: db is col.db)."""
    db.execute(f"ATTACH DATABASE '{os.fspath(gap_path)}' AS {alias}")


def ensure_sidecar_schema(gap_path: str | os.PathLike,
                          sql_root: str | os.PathLike | None = None) -> None:
    """Create/upgrade the sidecar's tables **in the sidecar file**, using a
    dedicated SQLite connection so the unqualified `CREATE TABLE`s land in gap.db
    and never in the collection. This is the ONLY correct way to apply the schema
    in the real add-on: run it against gap.db directly, then `attach_gap` the
    populated file onto `col.db`. Idempotent.
    """
    con = sqlite3.connect(os.fspath(gap_path))
    try:
        GapDB(SqliteProxy(con), sql_root=sql_root).apply_schema()
        con.commit()
    finally:
        con.close()


def open_sidecar(main_path: str | os.PathLike | None,
                 gap_path: str | os.PathLike,
                 main_stub: bool = False,
                 sql_root: str | os.PathLike | None = None) -> GapDB:
    """Open a standalone connection with `gap` attached, for the simulation and
    tests that do not go through the Anki backend. When `main_stub` is set, the
    connection is seeded with `schema/main_stub.sql` so `main.*` joins resolve.
    The real add-on does NOT use this — it wraps Anki's own `col.db` (see
    `ensure_sidecar_schema` + `attach_gap`).
    """
    ensure_sidecar_schema(gap_path, sql_root=sql_root)   # schema lives in gap.db
    con = sqlite3.connect(main_path or ":memory:")
    proxy = SqliteProxy(con)
    gapdb = GapDB(proxy, sql_root=sql_root)
    if main_stub:
        for st in split_statements(gapdb.sql_text("schema/main_stub.sql")):
            con.execute(st)
    attach_gap(proxy, gap_path)
    return gapdb
