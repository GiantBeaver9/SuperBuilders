#!/usr/bin/env python3
"""Corruption-resilience test — SIGKILL the sidecar writer mid-write, 20x.

The PRD's resilience story: the append-only sidecar ``gap.db`` is a **separate
file** from Anki's ``collection.anki2``. A crash (or power loss) while the add-on
is writing novel attempts must never (a) corrupt the sidecar or (b) touch the
collection. This test proves both, hard.

Each of ``ITERATIONS`` (default 20) rounds:

  1. Copy a pre-built base ``gap.db`` to a fresh per-round file.
  2. Spawn a **subprocess** (``--writer``) that opens ONLY the sidecar, switches
     it to WAL, and streams novel attempts — each attempt a single atomic
     transaction (novel_item + its concept link + its revlog row, committed
     together).
  3. ``SIGKILL`` that subprocess at a random instant mid-write (simulated crash /
     power loss — no chance to flush, no cleanup handlers).
  4. Reopen the sidecar in the parent and verify:
       * ``PRAGMA integrity_check == 'ok'`` (file structurally sound after WAL
         recovery),
       * the schema is fully intact (every gap table present),
       * the append-only invariants hold — no orphaned / half-written / duplicate
         rows (a killed-mid-transaction attempt rolls back **whole**: no revlog
         without its item, no item without its concept link),
       * the ``collection.anki2`` stub is **byte-for-byte unchanged** (SHA-256),
         because the writer never opened it.

Corruptions are counted; the run asserts **0 across all iterations**.

JOURNAL MODE. The writer runs the sidecar in **WAL** (``PRAGMA journal_mode=WAL``)
with ``synchronous=FULL``. WAL gives atomic-commit + crash recovery: a killed
process leaves committed transactions intact and rolls back the in-flight one on
the next open. ``synchronous=FULL`` is what extends the guarantee from a mere
process kill to true power loss (the WAL frames + header are fsync'd before a
commit is acknowledged). Anki's own default rollback journal (DELETE) gives the
same atomicity; WAL is chosen here because it is the mode the add-on runs the
sidecar in for concurrent read-during-write.

Run:  python3 bench/crash_test.py               # 20 iterations
      CRASH_ITERS=3 python3 bench/crash_test.py  # quick
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Ranges kept clear of the seeded ids so writer rows are always new inserts.
_ITEM_BASE = 9_000_000_000_000
_REV_BASE = 8_000_000_000_000

_EXPECTED_TABLES = {
    "meta", "concepts", "note_concepts", "novel_items",
    "novel_item_concepts", "novel_revlog", "arms", "retirements",
}


# --------------------------------------------------------------------------- #
# writer subprocess — opens ONLY the sidecar, streams atomic novel attempts
# --------------------------------------------------------------------------- #
def _writer(gap_path: str) -> None:
    """Stream novel attempts into the sidecar until SIGKILLed. Each attempt is one
    atomic transaction, so a mid-write kill can only ever leave whole attempts."""
    con = sqlite3.connect(gap_path, isolation_level=None)   # explicit txn control
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    # Link every writer attempt to concept 1 (guaranteed to exist in the base).
    concept_id = con.execute("SELECT id FROM concepts ORDER BY id LIMIT 1").fetchone()[0]
    pid = os.getpid()
    i = 0
    # Bounded loop so a missed kill can't spin forever; the parent kills long
    # before this is exhausted.
    while i < 5_000_000:
        item_id = _ITEM_BASE + pid * 10_000_000 + i
        rev_id = _REV_BASE + pid * 10_000_000 + i
        con.execute("BEGIN")
        con.execute(
            "INSERT INTO novel_items(id,guid,source_id,is_holdout,usn,mod)"
            " VALUES(?,?,?,?,?,?)",
            (item_id, f"crash-{pid}-{i}", "crash", 0, -1, 0))
        con.execute(
            "INSERT INTO novel_item_concepts(item_id,concept_id) VALUES(?,?)",
            (item_id, concept_id))
        con.execute(
            "INSERT INTO novel_revlog(id,item_id,correct,time,usn) VALUES(?,?,?,?,?)",
            (rev_id, item_id, i & 1, 3000, -1))
        con.execute("COMMIT")
        i += 1


# --------------------------------------------------------------------------- #
# base dataset (built once, on disk, then closed so files are flushed)
# --------------------------------------------------------------------------- #
def _build_base(work: Path, n_cards: int, m_concepts: int) -> tuple[Path, Path]:
    """Build a base ``main_stub`` + ``gap.db`` on disk and close the connection so
    both files are fully flushed and copyable."""
    from bench.seed_large import build_dataset
    main_path = work / "collection.anki2"
    gap_base = work / "gap_base.db"
    g = build_dataset(n_cards=n_cards, m_concepts=m_concepts,
                      main_path=str(main_path), gap_path=str(gap_base))
    g.db.con.close()                       # flush + release both files
    return main_path, gap_base


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# verification of a post-crash sidecar
# --------------------------------------------------------------------------- #
def _verify_sidecar(gap_path: Path) -> tuple[bool, list[str], int]:
    """Reopen the crashed sidecar and check every invariant.

    Returns ``(clean, problems, writer_rows_committed)``.
    """
    problems: list[str] = []
    con = sqlite3.connect(str(gap_path))
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            problems.append(f"integrity_check={integrity!r}")

        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = _EXPECTED_TABLES - tables
        if missing:
            problems.append(f"missing tables: {sorted(missing)}")

        # append-only atomicity: no orphaned or half-written rows.
        orphan_rev = con.execute(
            "SELECT COUNT(*) FROM novel_revlog nr "
            "WHERE NOT EXISTS (SELECT 1 FROM novel_items ni WHERE ni.id = nr.item_id)"
        ).fetchone()[0]
        if orphan_rev:
            problems.append(f"{orphan_rev} orphan novel_revlog rows")

        orphan_link = con.execute(
            "SELECT COUNT(*) FROM novel_item_concepts nic "
            "WHERE NOT EXISTS (SELECT 1 FROM novel_items ni WHERE ni.id = nic.item_id)"
        ).fetchone()[0]
        if orphan_link:
            problems.append(f"{orphan_link} orphan novel_item_concepts rows")

        item_no_link = con.execute(
            "SELECT COUNT(*) FROM novel_items ni "
            "WHERE NOT EXISTS (SELECT 1 FROM novel_item_concepts nic WHERE nic.item_id = ni.id)"
        ).fetchone()[0]
        if item_no_link:
            problems.append(f"{item_no_link} novel_items with no concept link (half-written)")

        item_no_rev = con.execute(
            "SELECT COUNT(*) FROM novel_items ni "
            "WHERE NOT EXISTS (SELECT 1 FROM novel_revlog nr WHERE nr.item_id = ni.id)"
        ).fetchone()[0]
        if item_no_rev:
            problems.append(f"{item_no_rev} novel_items with no revlog (half-written)")

        # no duplicate ids (append-only means every id appears once).
        for tbl in ("novel_items", "novel_revlog"):
            total, distinct = con.execute(
                f"SELECT COUNT(id), COUNT(DISTINCT id) FROM {tbl}").fetchone()
            if total != distinct:
                problems.append(f"{tbl}: {total - distinct} duplicate ids")

        writer_rows = con.execute(
            "SELECT COUNT(*) FROM novel_items WHERE source_id = 'crash'").fetchone()[0]
    finally:
        con.close()
    return (len(problems) == 0, problems, writer_rows)


def _cleanup(gap_path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(gap_path) + suffix)
        if p.exists():
            p.unlink()


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def run_crash_test(iterations: int = 20, n_cards: int = 2000,
                   m_concepts: int = 50, seed: int = 7) -> dict:
    """Run ``iterations`` kill-mid-write rounds. Returns a summary dict."""
    import subprocess
    rnd = random.Random(seed)

    with tempfile.TemporaryDirectory(prefix="crashtest_") as td:
        work = Path(td)
        main_path, gap_base = _build_base(work, n_cards, m_concepts)
        main_hash_before = _sha256(main_path)

        corruptions = 0
        mid_write_rounds = 0
        all_problems: list[str] = []
        per_round: list[dict] = []

        for it in range(1, iterations + 1):
            gap_iter = work / f"gap_{it}.db"
            shutil.copy2(gap_base, gap_iter)

            p = subprocess.Popen([sys.executable, __file__, "--writer", str(gap_iter)])
            # Let the writer get going, then crash it at a random mid-write instant.
            time.sleep(rnd.uniform(0.02, 0.20))
            p.kill()
            p.wait()

            clean, problems, writer_rows = _verify_sidecar(gap_iter)
            main_hash_after = _sha256(main_path)
            collection_untouched = (main_hash_after == main_hash_before)
            if not collection_untouched:
                problems.append("collection.anki2 was modified!")
                clean = False

            if writer_rows > 0:
                mid_write_rounds += 1
            if not clean:
                corruptions += 1
                all_problems.extend(f"iter {it}: {p_}" for p_ in problems)

            per_round.append({
                "iter": it, "clean": clean, "writer_rows": writer_rows,
                "collection_untouched": collection_untouched,
            })
            print(f"  iter {it:>2}/{iterations}: "
                  f"{'CLEAN' if clean else 'CORRUPT'}  "
                  f"committed={writer_rows:<5} collection_untouched={collection_untouched}")
            _cleanup(gap_iter)

        return {
            "iterations": iterations,
            "corruptions": corruptions,
            "mid_write_rounds": mid_write_rounds,
            "collection_untouched_all": all(r["collection_untouched"] for r in per_round),
            "journal_mode": "WAL (synchronous=FULL)",
            "problems": all_problems,
            "per_round": per_round,
            "n_cards": n_cards,
            "m_concepts": m_concepts,
        }


def format_report(summary: dict) -> str:
    ok = summary["corruptions"] == 0 and summary["collection_untouched_all"]
    lines = ["<!-- crash-test-results -->", "", "# Crash / corruption-resilience test\n"]
    lines.append(f"- **Iterations:** {summary['iterations']} (SIGKILL the sidecar "
                 f"writer at a random mid-write instant)")
    lines.append(f"- **Base dataset:** {summary['n_cards']:,} cards / "
                 f"{summary['m_concepts']} concepts on disk")
    lines.append(f"- **Journal mode:** {summary['journal_mode']}")
    lines.append(f"- **Corrupted sidecars:** {summary['corruptions']} / "
                 f"{summary['iterations']}")
    lines.append(f"- **Rounds where the kill landed mid-write (attempts committed "
                 f"before the crash):** {summary['mid_write_rounds']} / "
                 f"{summary['iterations']}")
    lines.append(f"- **`collection.anki2` untouched every round:** "
                 f"{summary['collection_untouched_all']}")
    lines.append("")
    lines.append(f"## Result: {'PASS' if ok else 'FAIL'} — "
                 f"{summary['iterations'] - summary['corruptions']}/"
                 f"{summary['iterations']} clean\n")
    if summary["problems"]:
        lines.append("### Problems\n")
        for p in summary["problems"]:
            lines.append(f"- {p}")
        lines.append("")
    lines.append("Each novel attempt is a single atomic transaction "
                 "(item + concept link + revlog). A kill mid-transaction rolls the "
                 "whole attempt back on the next open, so the sidecar only ever holds "
                 "complete attempts — verified by the orphan/half-write/duplicate "
                 "checks above. The collection stays byte-for-byte identical because "
                 "the writer never opens it: the sidecar is a physically separate "
                 "file.\n")
    return "\n".join(lines)


def main() -> int:
    iters = int(os.environ.get("CRASH_ITERS", "20"))
    n = int(os.environ.get("CRASH_N", "2000"))
    m = int(os.environ.get("CRASH_M", "50"))
    print(f"# crash test: {iters} iterations, {n}-card base sidecar\n")
    summary = run_crash_test(iterations=iters, n_cards=n, m_concepts=m)
    report = format_report(summary)
    print("\n" + report)

    out = Path(os.environ.get("RESULTS_MD", _REPO / "bench" / "RESULTS.md"))
    marker = "\n<!-- crash-test-results -->\n"
    head = ""
    if out.exists():
        text = out.read_text()
        head = text.split(marker, 1)[0] if marker in text else text
    # Keep the benchmark section (if any) and replace/append the crash section.
    sep = "\n" if head and not head.endswith("\n") else ""
    out.write_text(head + sep + "\n" + report)
    print(f"wrote {out}")

    assert summary["corruptions"] == 0, \
        f"CORRUPTION DETECTED: {summary['corruptions']} of {iters} sidecars corrupted"
    assert summary["collection_untouched_all"], "collection.anki2 was modified"
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--writer":
        _writer(sys.argv[2])                # subprocess entry — never returns cleanly
    else:
        raise SystemExit(main())
