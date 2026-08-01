#!/usr/bin/env python3
"""Assemble an installable `.ankiaddon` zip.

An `.ankiaddon` file is a plain zip whose ROOT contains the add-on's
`__init__.py` (no wrapping top-level folder). This script stages the add-on
directory, drops the pure engine (`gap/`) and the committed SQL beside it, and
zips the staging root.

## How the bundled SQL is made resolvable

`gap/db.py::_resolve_sql_root` looks for a `sql/` directory *next to the `gap`
package* — i.e. `gap/sql/` — that holds `schema/gap.sql` and `queries/`. In dev
it walks up to the repo root instead; inside an installed add-on there is no repo
root, so we bundle the SQL at **`gap/sql/schema`** and **`gap/sql/queries`**. No
environment variable is required at runtime; `GAP_SQL_ROOT` remains an optional
override. (See `_resolve_sql_root`'s candidate order.)

Run:  python3 scripts/bundle_addon.py
Output: dist/novel_item_gate.ankiaddon
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "addon"
GAP = REPO / "gap"
SCHEMA = REPO / "schema"
QUERIES = REPO / "queries"
DIST = REPO / "dist"
BUILD = REPO / "build" / "addon_stage"

# Files/dirs never copied into the bundle.
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")


def _package_name() -> str:
    manifest = json.loads((ADDON / "manifest.json").read_text())
    return manifest.get("package", "novel_item_gate")


def _stage() -> Path:
    """Build the staging tree; return its root (== the .ankiaddon zip root)."""
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    # 1. add-on files (__init__.py, manifest, config, ui/) at the zip root.
    shutil.copytree(ADDON, BUILD, dirs_exist_ok=True, ignore=_IGNORE)

    # 2. the pure engine package, as top-level `gap` inside the add-on.
    shutil.copytree(GAP, BUILD / "gap", ignore=_IGNORE)

    # 3. the committed SQL, at gap/sql/{schema,queries} so _resolve_sql_root
    #    finds it via its `here.parent / "sql"` candidate (here = gap/db.py).
    sql_root = BUILD / "gap" / "sql"
    shutil.copytree(SCHEMA, sql_root / "schema", ignore=_IGNORE)
    shutil.copytree(QUERIES, sql_root / "queries", ignore=_IGNORE)

    return BUILD


def _zip(stage: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(stage).as_posix())


def _verify(out: Path) -> None:
    """Sanity-check the zip has the add-on entry point and bundled SQL at root."""
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    required = {
        "__init__.py",
        "manifest.json",
        "gap/db.py",
        "gap/service.py",
        "gap/sql/schema/gap.sql",
        "gap/sql/queries/01_open/rebuild_note_concepts.sql",
    }
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"bundle is missing required members: {missing}")


def main() -> int:
    stage = _stage()
    out = DIST / f"{_package_name()}.ankiaddon"
    _zip(stage, out)
    _verify(out)
    with zipfile.ZipFile(out) as zf:
        count = len(zf.namelist())
    print(f"built {out}  ({count} members)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
