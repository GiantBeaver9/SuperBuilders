#!/usr/bin/env python3
"""Plain-python tests for gap.stats + the simulation (asserts, then prints OK).

Covers:
  1. dashboard_payload runs on an `open_sidecar` DB with seeded rows, exposes the
     EXACT documented top-level keys, uses abstain threshold 8, and is
     JSON-serializable (json.dumps round-trips).
  2. `python3 sim/simulate.py` exits 0 and writes sim/dashboard_data.json whose
     crossover shows diff_pp NEGATIVE at bucket '1-4' and POSITIVE at '5+'
     (the pre-registered sign flip).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap import stats           # noqa: E402
from gap.db import open_sidecar  # noqa: E402

EXPECTED_TOP_KEYS = {"generated_ms", "concepts", "abstain", "arms", "endpoints"}


def _seed(g):
    """Seed a small but complete scenario: 2 concepts (one gate, one nogate), one
    card + reviews each, practice + held-out novel attempts, arms and a retirement.
    Enough for every endpoint SQL to run and for the abstain block to populate."""
    con = g.db.con
    base = 1_700_000_000_000
    hour = 3_600_000
    for cid, code, arm in [(101, "T01", "gate"), (102, "T02", "nogate")]:
        cbase = base + cid * 100_000_000
        con.execute("INSERT INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
                    " VALUES(?,?,?,?,?)", (cid, code, f"C {code}", 1.5, 5.0))
        con.execute("INSERT INTO notes(id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, f"g{cid}", 1, 0, -1, f" concept::{code} ", "f", "f", 0, 0, "{}"))
        con.execute("INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
                    "reps,lapses,left,odue,odid,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, cid, 1, 0, 0, -1, 2, 2, 0, 30, 2500, 8, 0, 0, 0, 0, 0,
                     '{"s": 50.0, "d": 5.0}'))
        for k in range(1, 9):  # 8 exposures, time falling
            con.execute("INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (cbase + k * hour, cid, -1, 3, 10, 5, 2500, 9000 - 800 * k, 1))
        con.execute("INSERT INTO gap.arms(concept_id,arm,assigned_ms) VALUES(?,?,?)",
                    (cid, arm, base - hour))
        # 10 practice novel attempts (>=8 -> scored): 5 in bucket 1-4, 5 in 5+
        j = 0
        for e in (1, 2, 3, 4, 4, 5, 6, 7, 8, 8):
            item = 10_000_000_000 + cid * 100 + j
            rid = cbase + e * hour + 60_000 + j * 1000
            con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                        " VALUES(?,?,?,?,?,?)", (item, f"ni{item}", "s", 0, -1, 0))
            con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                        (item, cid))
            correct = 1 if (j % 2 == 0) ^ (arm == "nogate") else 0
            con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                        " VALUES(?,?,?,?,?)", (rid, item, correct, 5000, -1))
            j += 1
        # held-out attempts for the terminal endpoint
        for h in range(4):
            item = 20_000_000_000 + cid * 100 + h
            con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                        " VALUES(?,?,?,?,?,?)", (item, f"ho{item}", "s", 1, -1, 0))
            con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                        (item, cid))
            con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                        " VALUES(?,?,?,?,?)",
                        (cbase + 50_000_000 + h, item, 1 if arm == "gate" else 0, 5000, -1))
    # one persisted retirement (gate concept met the novel gate)
    con.execute("INSERT INTO gap.retirements(concept_id,retired_ms,trigger)"
                " VALUES(?,?,?)", (101, base + 20 * hour, "novel_gate"))
    g.commit()
    g.run_file("queries/01_open/rebuild_note_concepts.sql")
    g.commit()


def test_dashboard_payload():
    tmp = tempfile.mkdtemp(prefix="gapstat_")
    g = open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)
    _seed(g)

    payload = stats.dashboard_payload(g, at_ms=1_700_000_000_000 + 400 * 3_600_000)

    # exact top-level keys
    assert set(payload.keys()) == EXPECTED_TOP_KEYS, payload.keys()

    # abstain threshold is 8
    assert payload["abstain"]["threshold"] == 8, payload["abstain"]

    # per-concept shape carries the documented fields
    assert payload["concepts"], "expected concept rows"
    c0 = payload["concepts"][0]
    for key in ("concept_id", "code", "name", "arm", "weight", "novel_attempts",
                "has_score", "performance", "coverage_pct", "card_mastery",
                "retired", "retired_trigger"):
        assert key in c0, key
    # 10 practice attempts >= 8 -> scored with a real performance number
    assert c0["has_score"] is True and c0["performance"] is not None

    # arms + endpoints blocks present with expected sub-shape
    assert set(payload["arms"].keys()) >= {"gate", "nogate", "vanilla"}
    ep = payload["endpoints"]
    assert set(ep.keys()) == {"crossover", "terminal", "throughput", "latency"}
    assert isinstance(ep["latency"]["lockstep"], bool)
    assert [c["bucket"] for c in ep["crossover"]] == ["1-4", "5+"]

    # JSON-serializable
    s = json.dumps(payload)
    assert json.loads(s)["abstain"]["threshold"] == 8
    print("OK  test_dashboard_payload")


def test_simulation_and_crossover():
    proc = subprocess.run([sys.executable, str(ROOT / "sim" / "simulate.py")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"sim exited {proc.returncode}\n{proc.stderr}"

    out = ROOT / "sim" / "dashboard_data.json"
    assert out.is_file(), "sim did not write dashboard_data.json"
    payload = json.loads(out.read_text())

    xo = {c["bucket"]: c["diff_pp"] for c in payload["endpoints"]["crossover"]}
    assert xo.get("1-4") is not None and xo["1-4"] < 0, f"1-4 diff not negative: {xo}"
    assert xo.get("5+") is not None and xo["5+"] > 0, f"5+ diff not positive: {xo}"
    print(f"OK  test_simulation_and_crossover  (diff_pp 1-4={xo['1-4']:.1f}, 5+={xo['5+']:.1f})")


if __name__ == "__main__":
    test_dashboard_payload()
    test_simulation_and_crossover()
    print("OK")
