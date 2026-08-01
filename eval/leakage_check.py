#!/usr/bin/env python3
"""Runnable leakage gate — makes ``queries/05_discipline/leakage_check.sql`` a
rerunnable script (PRD §3, data discipline).

The committed leakage SQL is the source of truth for "no contamination": five
checks (a-e) covering holdout-flag sanity, a source_id straddling the
holdout/practice boundary, a held-out item practiced before first exposure, an
arm assigned after first exposure, and an invalid/orphan arm row. This wrapper
runs that SQL verbatim over an ``open_sidecar`` DB, prints each check's violation
count, and **exits non-zero if any check has > 0 violations** — so CI (or a
person) can assert cleanliness before any endpoint is computed.

Run ``python3 eval/leakage_check.py``: it seeds a clean, realistic scenario,
runs the gate, and exits 0. :func:`run_leakage_check` and :func:`seed_clean` are
importable so tests can point it at their own DB and inject a violation.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gap.db import GapDB, open_sidecar         # noqa: E402

LEAKAGE_SQL = "queries/05_discipline/leakage_check.sql"


def run_leakage_check(gapdb: GapDB) -> list[tuple[str, int]]:
    """Execute the committed leakage SQL and return its per-check summary.

    Runs both statements of ``leakage_check.sql`` verbatim (via
    :meth:`GapDB.query_all`); the second statement is the fixed-roster summary —
    one row ``(check_name, violation_count)`` per check, ``0`` == clean. Returns
    that list of tuples in ``check_name`` order.
    """
    result_sets = gapdb.query_all(LEAKAGE_SQL)
    summary = result_sets[-1]          # last SELECT is the per-check summary
    return [(str(name), int(count)) for name, count in summary]


def seed_clean(gapdb: GapDB) -> None:
    """Seed a small, discipline-clean scenario into an open sidecar.

    Two concepts, each with a card and an early card exposure; arms assigned
    *before* first exposure; practice novel items on the practice side; held-out
    items attempted *after* first exposure. Every leakage check should report 0.
    """
    con = gapdb.db.con
    base = 1_700_000_000_000
    hour = 3_600_000
    for cid, code, arm in [(201, "L01", "gate"), (202, "L02", "nogate")]:
        con.execute("INSERT INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
                    " VALUES(?,?,?,?,?)", (cid, code, f"C {code}", 1.0, 5.0))
        con.execute("INSERT INTO notes(id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, f"lg{cid}", 1, 0, -1, f" concept::{code} ", "f", "f", 0, 0, "{}"))
        con.execute("INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
                    "reps,lapses,left,odue,odid,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, cid, 1, 0, 0, -1, 2, 2, 0, 30, 2500, 3, 0, 0, 0, 0, 0,
                     '{"s": 50.0, "d": 5.0}'))
        first_exposure = base + cid * hour  # concept's first card review (id unique per concept)
        con.execute("INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (first_exposure, cid, -1, 3, 10, 5, 2500, 5000, 1))
        # arm assigned BEFORE first exposure (clean)
        con.execute("INSERT INTO gap.arms(concept_id,arm,assigned_ms) VALUES(?,?,?)",
                    (cid, arm, base - hour))
        # a practice novel item (is_holdout=0), source distinct from holdout source
        p_item = 30_000_000_000 + cid
        con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                    " VALUES(?,?,?,?,?,?)", (p_item, f"pi{p_item}", f"practice-src-{cid}", 0, -1, 0))
        con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                    (p_item, cid))
        con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                    " VALUES(?,?,?,?,?)", (first_exposure + hour, p_item, 1, 5000, -1))
        # a held-out item (is_holdout=1), attempted AFTER first exposure (clean)
        h_item = 40_000_000_000 + cid
        con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                    " VALUES(?,?,?,?,?,?)", (h_item, f"hi{h_item}", f"holdout-src-{cid}", 1, -1, 0))
        con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                    (h_item, cid))
        con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                    " VALUES(?,?,?,?,?)", (first_exposure + 100 * hour, h_item, 1, 5000, -1))
    gapdb.commit()
    gapdb.run_file("queries/01_open/rebuild_note_concepts.sql")
    gapdb.commit()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="gapleak_")
    g = open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)
    seed_clean(g)
    summary = run_leakage_check(g)

    print("leakage_check.sql — per-check violation counts:")
    total = 0
    for name, count in summary:
        flag = "CLEAN" if count == 0 else "VIOLATION"
        print(f"  {name:<32} {count:>4}   {flag}")
        total += count
    if total == 0:
        print("RESULT: CLEAN (all checks 0)")
        return 0
    print(f"RESULT: DIRTY ({total} total violations)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
