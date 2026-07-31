#!/usr/bin/env python3
"""Validate query files against the schema contract.

Builds `main` from schema/main_stub.sql, ATTACHes a fresh gap.db from
schema/gap.sql, then runs each target .sql file inside a transaction that is
rolled back. Empty tables mean zero rows, but SQLite still prepares and steps
every statement, so unknown columns, bad table refs, and syntax errors all
surface. Append-only/idempotent operational files run harmlessly and roll back.

Usage:
    scripts/validate_sql.py                 # validate every queries/**/*.sql
    scripts/validate_sql.py path/to/one.sql # validate a single file

Exit status is nonzero if any file fails.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAP_SQL = ROOT / "schema" / "gap.sql"
MAIN_SQL = ROOT / "schema" / "main_stub.sql"
QUERIES = ROOT / "queries"

MIN_SQLITE = (3, 37, 0)  # STRICT tables


def build_conn(tmpdir: Path) -> sqlite3.Connection:
    gap_path = tmpdir / "gap.db"
    gap = sqlite3.connect(gap_path)
    gap.executescript(GAP_SQL.read_text())
    gap.commit()
    gap.close()

    con = sqlite3.connect(":memory:")
    con.executescript(MAIN_SQL.read_text())
    con.execute("ATTACH DATABASE ? AS gap", (str(gap_path),))
    return con


def validate_file(con: sqlite3.Connection, path: Path) -> str | None:
    """Return None on success, or an error string."""
    sql = path.read_text()
    try:
        con.execute("BEGIN")
        con.executescript(sql)
        return None
    except sqlite3.Error as e:
        return f"{type(e).__name__}: {e}"
    finally:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def main() -> int:
    have = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if have < MIN_SQLITE:
        print(f"FAIL  sqlite {sqlite3.sqlite_version} < 3.37 (STRICT unsupported)")
        return 2

    if len(sys.argv) > 1:
        targets = [Path(a).resolve() for a in sys.argv[1:]]
    else:
        targets = sorted(QUERIES.rglob("*.sql"))

    if not targets:
        print("no .sql files to validate")
        return 0

    with tempfile.TemporaryDirectory() as td:
        con = build_conn(Path(td))
        failures = 0
        for path in targets:
            rel = path.relative_to(ROOT) if ROOT in path.parents else path
            err = validate_file(con, path)
            if err is None:
                print(f"OK    {rel}")
            else:
                failures += 1
                print(f"FAIL  {rel}\n      {err}")
        con.close()

    print(f"\n{len(targets) - failures}/{len(targets)} files valid "
          f"(sqlite {sqlite3.sqlite_version})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
