"""Sync reconciliation tests — plain python asserts, prints OK at the end.

Proves the CRDT-style properties of sync/reconcile.py directly (idempotent,
commutative, lossless, earliest-wins), then that the two shippable checks pass:
the PRD §6 integrity harness exits 0, and the sync .proto compiles with protoc.

Run:  cd /home/user/SuperBuilders && python3 tests/test_sync.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sync.reconcile import reconcile, reconcile_into, row_snapshot  # noqa: E402
from sync.integrity_test import (  # noqa: E402
    seed_base, record_attempts, add_item, set_arm, set_retirement,
    revlog_ids, measurement_snapshot,
)


def _two_diverged(workdir: str):
    """Build base + two offline-diverged copies (phone/desktop) like the PRD
    scenario, with 10 + 10 distinct attempts and conflicting arm/retirement."""
    base = os.path.join(workdir, "base.gap.db")
    a = os.path.join(workdir, "a.gap.db")
    b = os.path.join(workdir, "b.gap.db")
    seed_base(base)
    shutil.copyfile(base, a)
    shutil.copyfile(base, b)
    record_attempts(a, list(range(3000, 3010)), item_base=1000)   # 10 on A
    record_attempts(b, list(range(4000, 4010)), item_base=1010)   # 10 on B
    add_item(a, 5000, concept_id=2)
    add_item(b, 6000, concept_id=2)
    set_arm(a, 1, "gate", 100)      # B is earlier -> B wins
    set_arm(b, 1, "gate", 50)
    set_retirement(a, 1, 900, "novel_gate")
    set_retirement(b, 1, 800, "novel_gate")   # B earlier -> B wins
    return base, a, b


def test_preserves_all_attempts():
    """0 lost / 0 duplicated: the 20 attempts appear exactly once after merge."""
    wd = tempfile.mkdtemp(prefix="gap_sync_t1_")
    _, a, b = _two_diverged(wd)
    out = os.path.join(wd, "out.gap.db")
    reconcile(a, b, out)
    ids = revlog_ids(out)
    expected = sorted(list(range(3000, 3010)) + list(range(4000, 4010)))
    assert ids == expected, f"expected 20 unique attempts, got {ids}"
    assert len(ids) == len(set(ids)) == 20, "duplication or loss detected"


def test_commutative():
    """A⊕B and B⊕A produce value-equal measurement state."""
    wd = tempfile.mkdtemp(prefix="gap_sync_t2_")
    _, a, b = _two_diverged(wd)
    ab = reconcile(a, b, os.path.join(wd, "ab.gap.db"))
    ba = reconcile(b, a, os.path.join(wd, "ba.gap.db"))
    assert measurement_snapshot(ab) == measurement_snapshot(ba), \
        "reconcile is not commutative"


def test_idempotent():
    """Re-merging an already-merged pair changes nothing."""
    wd = tempfile.mkdtemp(prefix="gap_sync_t3_")
    _, a, b = _two_diverged(wd)
    reconcile_into(a, b)                 # a := a ∪ b
    before = measurement_snapshot(a)
    reconcile_into(a, b)                 # merge b again
    reconcile_into(a, a_copy(a, wd))     # merge a into itself
    after = measurement_snapshot(a)
    assert before == after, "reconcile is not idempotent"


def a_copy(path: str, wd: str) -> str:
    dst = os.path.join(wd, "acopy.gap.db")
    shutil.copyfile(path, dst)
    return dst


def test_associative():
    """(A⊕B)⊕C == A⊕(B⊕C) on row sets — a third offline device joins."""
    wd = tempfile.mkdtemp(prefix="gap_sync_t4_")
    base, a, b = _two_diverged(wd)
    c = os.path.join(wd, "c.gap.db")
    shutil.copyfile(base, c)
    record_attempts(c, list(range(5000, 5005)), item_base=1000)   # 5 more on C
    # (A⊕B)⊕C
    left = reconcile(reconcile(a, b, os.path.join(wd, "ab.gap.db")), c,
                     os.path.join(wd, "abc.gap.db"))
    # A⊕(B⊕C)
    right = reconcile(a, reconcile(b, c, os.path.join(wd, "bc.gap.db")),
                      os.path.join(wd, "a_bc.gap.db"))
    assert measurement_snapshot(left) == measurement_snapshot(right), \
        "reconcile is not associative"


def test_earliest_wins():
    """arms/retirements keep the EARLIEST timestamp regardless of merge order."""
    wd = tempfile.mkdtemp(prefix="gap_sync_t5_")
    _, a, b = _two_diverged(wd)
    for out in (reconcile(a, b, os.path.join(wd, "ab.gap.db")),
                reconcile(b, a, os.path.join(wd, "ba.gap.db"))):
        con = sqlite3.connect(out)
        try:
            assert con.execute(
                "SELECT assigned_ms FROM arms WHERE concept_id=1").fetchone()[0] == 50, \
                "arms did not keep earliest assigned_ms"
            assert con.execute(
                "SELECT retired_ms FROM retirements WHERE concept_id=1").fetchone()[0] == 800, \
                "retirements did not keep earliest retired_ms"
        finally:
            con.close()


def test_inputs_untouched():
    """reconcile() must not mutate its two source sidecars."""
    wd = tempfile.mkdtemp(prefix="gap_sync_t6_")
    _, a, b = _two_diverged(wd)
    snap_a, snap_b = row_snapshot(a), row_snapshot(b)
    reconcile(a, b, os.path.join(wd, "out.gap.db"))
    assert row_snapshot(a) == snap_a, "reconcile mutated source A"
    assert row_snapshot(b) == snap_b, "reconcile mutated source B"


def test_integrity_harness_exits_zero():
    """The PRD §6 harness runs standalone and exits 0 with PASS."""
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "sync", "integrity_test.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"integrity_test.py exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "PASS" in proc.stdout, f"integrity_test.py did not PASS:\n{proc.stdout}"


def test_proto_compiles():
    """The sync contract compiles with the installed protoc."""
    proto_root = os.path.join(ROOT, "proto")
    proc = subprocess.run(
        ["protoc", "--proto_path", proto_root, "--descriptor_set_out", os.devnull,
         os.path.join(proto_root, "sync_gap.proto")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"protoc failed:\n{proc.stderr}"


def main() -> None:
    tests = [
        test_preserves_all_attempts,
        test_commutative,
        test_idempotent,
        test_associative,
        test_earliest_wins,
        test_inputs_untouched,
        test_integrity_harness_exits_zero,
        test_proto_compiles,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print("OK")


if __name__ == "__main__":
    main()
