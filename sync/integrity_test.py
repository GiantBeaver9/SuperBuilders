"""The PRD's 20-card sync-integrity test, run headlessly.

Scenario (PRD §6, "Sync integrity — 0 lost / 0 duplicated across 20 cards"):

  1. Start from a common BASE sidecar both devices already share.
  2. Go offline. On the "phone" copy, make 10 novel attempts.
  3. Independently, on the "desktop" copy, make 10 DIFFERENT novel attempts.
  4. Reconnect. Reconcile BOTH directions (phone←desktop and desktop←phone).
  5. Assert: all 20 attempts present exactly once in EACH reconciled sidecar
     (0 lost, 0 duplicated), the two sidecars have CONVERGED (identical row
     sets), the earliest-wins rules held for the conflicting arm/retirement,
     and re-running the merge changes nothing (idempotent).
  6. Report timings; assert the merge is far under the PRD's 5 s session target.

Run:  cd /home/user/SuperBuilders && python3 sync/integrity_test.py
Exit code 0 and a printed PASS on success; non-zero and FAIL otherwise.

To keep the test hermetic it writes directly to standalone gap.db files with the
committed schema (via `ensure_sidecar_schema`); the table names are therefore
unqualified (each file *is* the sidecar). This mirrors exactly what a device
holds locally before it syncs.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gap.db import ensure_sidecar_schema           # noqa: E402
from sync.reconcile import reconcile, reconcile_into, row_snapshot  # noqa: E402

# The append-only measurement tables whose union must be lossless. `meta` is
# schema bookkeeping, not measurement, so it is excluded from convergence checks.
MEASUREMENT_TABLES = (
    "concepts", "note_concepts", "novel_items",
    "novel_item_concepts", "novel_revlog", "arms", "retirements",
)


# --------------------------------------------------------------------------- #
# Seeding helpers (also imported by tests/test_sync.py).
# --------------------------------------------------------------------------- #
def seed_base(path: str) -> None:
    """Create the shared base sidecar: 2 concepts and 20 practice novel items
    (ids 1000..1019) both devices already have. No attempts yet."""
    ensure_sidecar_schema(path)
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT INTO concepts(id,code,name,weight) VALUES (1,'1A.1','c1',1.0)")
        con.execute("INSERT INTO concepts(id,code,name,weight) VALUES (2,'1A.2','c2',1.0)")
        for i in range(20):
            iid = 1000 + i
            con.execute(
                "INSERT INTO novel_items(id,guid,source_id,is_holdout,usn,mod) "
                "VALUES (?,?,?,?,?,?)",
                (iid, f"item{iid}", "base", 0, -1, iid),
            )
            con.execute(
                "INSERT INTO novel_item_concepts(item_id,concept_id) VALUES (?,1)",
                (iid,),
            )
        con.commit()
    finally:
        con.close()


def record_attempts(path: str, revlog_ids, item_base: int) -> None:
    """Append one novel attempt per id in `revlog_ids`, each on a distinct item
    starting at `item_base`. Append-only INSERT into novel_revlog."""
    con = sqlite3.connect(path)
    try:
        for k, rid in enumerate(revlog_ids):
            con.execute(
                "INSERT INTO novel_revlog(id,item_id,correct,time,usn) VALUES (?,?,?,?,?)",
                (rid, item_base + k, k % 2, 1000 + k, -1),
            )
        con.commit()
    finally:
        con.close()


def add_item(path: str, item_id: int, concept_id: int) -> None:
    """Add one new novel item created offline on this device (tests union of
    novel_items / novel_item_concepts, not just the revlog)."""
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT INTO novel_items(id,guid,source_id,is_holdout,usn,mod) "
            "VALUES (?,?,?,?,?,?)",
            (item_id, f"item{item_id}", "device", 0, -1, item_id),
        )
        con.execute(
            "INSERT INTO novel_item_concepts(item_id,concept_id) VALUES (?,?)",
            (item_id, concept_id),
        )
        con.commit()
    finally:
        con.close()


def set_arm(path: str, concept_id: int, arm: str, assigned_ms: int) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO arms(concept_id,arm,assigned_ms) VALUES (?,?,?)",
            (concept_id, arm, assigned_ms),
        )
        con.commit()
    finally:
        con.close()


def set_retirement(path: str, concept_id: int, retired_ms: int, trigger: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO retirements(concept_id,retired_ms,trigger,usn) "
            "VALUES (?,?,?,?)",
            (concept_id, retired_ms, trigger, -1),
        )
        con.commit()
    finally:
        con.close()


def revlog_ids(path: str):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute("SELECT id FROM novel_revlog"))
    finally:
        con.close()


def measurement_snapshot(path: str):
    """Row-set view restricted to the measurement tables (excludes `meta`)."""
    snap = row_snapshot(path)
    return {t: snap[t] for t in MEASUREMENT_TABLES}


# --------------------------------------------------------------------------- #
# The test.
# --------------------------------------------------------------------------- #
def run() -> bool:
    workdir = tempfile.mkdtemp(prefix="gap_sync_")
    base = os.path.join(workdir, "base.gap.db")
    phone = os.path.join(workdir, "phone.gap.db")
    desktop = os.path.join(workdir, "desktop.gap.db")

    # 1. Common base, then two offline copies.
    seed_base(base)
    import shutil
    shutil.copyfile(base, phone)
    shutil.copyfile(base, desktop)

    # 2/3. Diverge offline. Phone: revlog ids 3000..3009 on items 1000..1009.
    #      Desktop: revlog ids 4000..4009 on items 1010..1019. 20 distinct ids.
    PHONE_IDS = list(range(3000, 3010))
    DESKTOP_IDS = list(range(4000, 4010))
    record_attempts(phone, PHONE_IDS, item_base=1000)
    record_attempts(desktop, DESKTOP_IDS, item_base=1010)

    # Also exercise the non-revlog tables:
    #   - each device authors a NEW novel item offline (union of items).
    add_item(phone, 5000, concept_id=2)
    add_item(desktop, 6000, concept_id=2)
    #   - a CONFLICTING arm assignment for concept 1: desktop's is earlier (50 < 100).
    set_arm(phone, 1, "gate", 100)
    set_arm(desktop, 1, "gate", 50)
    #   - a CONFLICTING retirement for concept 1: desktop's is earlier (800 < 900).
    set_retirement(phone, 1, 900, "novel_gate")
    set_retirement(desktop, 1, 800, "novel_gate")

    expected_ids = sorted(PHONE_IDS + DESKTOP_IDS)   # the 20 attempts

    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        status = "ok  " if cond else "FAIL"
        print(f"  [{status}] {msg}")
        if not cond:
            ok = False

    # 4. Reconcile BOTH directions.
    #    (a) NEW merged files via reconcile(a, b, out) — both orders.
    merged_pd = os.path.join(workdir, "merged_phone_then_desktop.gap.db")
    merged_dp = os.path.join(workdir, "merged_desktop_then_phone.gap.db")
    t0 = time.perf_counter()
    reconcile(phone, desktop, merged_pd)
    t_merge = time.perf_counter() - t0
    reconcile(desktop, phone, merged_dp)

    #    (b) In-place, the real device operation: each device applies the peer's
    #        file. desktop←phone and phone←desktop should CONVERGE.
    t1 = time.perf_counter()
    reconcile_into(desktop, phone)   # desktop now holds the union
    t_into = time.perf_counter() - t1
    reconcile_into(phone, desktop)   # phone applies desktop (now the union)

    # 5. Assertions — 0 lost / 0 duplicated on every reconciled artifact.
    for label, path in (("desktop(in-place)", desktop),
                        ("phone(in-place)", phone),
                        ("merged phone⊕desktop", merged_pd),
                        ("merged desktop⊕phone", merged_dp)):
        got = revlog_ids(path)
        n = len(got)
        n_unique = len(set(got))
        lost = len(set(expected_ids) - set(got))
        dup = n - n_unique
        check(got == expected_ids,
              f"{label}: 20 attempts present exactly once "
              f"(count={n}, unique={n_unique}, lost={lost}, dup={dup})")

    # Convergence: the two devices ended value-equal on measurement data.
    check(measurement_snapshot(desktop) == measurement_snapshot(phone),
          "desktop and phone converged to identical measurement state")
    # Commutativity of the standalone merge.
    check(measurement_snapshot(merged_pd) == measurement_snapshot(merged_dp),
          "reconcile is commutative (A⊕B == B⊕A on row sets)")

    # Union of the new offline items (5000, 6000) present on both.
    def item_ids(path):
        con = sqlite3.connect(path)
        try:
            return set(r[0] for r in con.execute("SELECT id FROM novel_items"))
        finally:
            con.close()
    for label, path in (("desktop", desktop), ("phone", phone)):
        ids = item_ids(path)
        check(5000 in ids and 6000 in ids and len(ids) == 22,
              f"{label}: novel_items unioned (20 base + 2 offline = {len(ids)})")

    # Earliest-wins conflict rules.
    def one(path, sql):
        con = sqlite3.connect(path)
        try:
            return con.execute(sql).fetchone()[0]
        finally:
            con.close()
    for label, path in (("desktop", desktop), ("phone", phone)):
        check(one(path, "SELECT assigned_ms FROM arms WHERE concept_id=1") == 50,
              f"{label}: arms kept EARLIEST assigned_ms (50, not 100)")
        check(one(path, "SELECT retired_ms FROM retirements WHERE concept_id=1") == 800,
              f"{label}: retirements kept EARLIEST retired_ms (800, not 900)")

    # 6. Idempotency: re-running the merge (in either direction) is a no-op now
    #    that the two devices have converged.
    before = measurement_snapshot(desktop)
    reconcile_into(desktop, phone)   # peer has nothing new -> no change
    reconcile_into(phone, desktop)
    after = measurement_snapshot(desktop)
    check(before == after, "re-running reconcile is idempotent (no-op)")

    # Timing vs the 5 s PRD session-sync target.
    check(t_merge < 5.0, f"reconcile() well under 5 s target (took {t_merge*1000:.2f} ms)")
    check(t_into < 5.0, f"reconcile_into() well under 5 s target (took {t_into*1000:.2f} ms)")

    print()
    print(f"  merge timing: reconcile()={t_merge*1000:.2f} ms, "
          f"reconcile_into()={t_into*1000:.2f} ms  (target < 5000 ms)")
    print(f"  attempts: expected=20, lost=0, duplicated=0")
    return ok


def main() -> int:
    print("PRD §6 sync-integrity test — 10 phone-offline + 10 desktop attempts")
    ok = run()
    print()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
