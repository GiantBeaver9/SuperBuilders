#!/usr/bin/env python3
"""Plain-python tests for the scoring + calibration layer (asserts, then prints OK).

Covers, on ``open_sidecar`` seeded data:
  1. performance_score abstains at n < 8 and yields a Wilson interval at n >= 8.
  2. memory_score is in [0,1] with lo <= value <= hi.
  3. readiness_score widens its CI as coverage drops (and abstains below the line).
  4. brier / log_loss match hand-computed values on a tiny fixture.
  5. reliability bins sum to n (every review lands in exactly one bin).
  6. leakage_check reports clean on clean data and flags an injected
     holdout-boundary (source-spans-holdout) violation.
  7. each runnable script (eval/calibration.py, eval/leakage_check.py) exits 0.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap.db import open_sidecar                                   # noqa: E402
from scoring import scores                                       # noqa: E402
from eval import calibration                                     # noqa: E402
from eval import leakage_check                                   # noqa: E402


# --------------------------------------------------------------------------- #
# seeding for the scoring tests
# --------------------------------------------------------------------------- #
def _new_db():
    tmp = tempfile.mkdtemp(prefix="gapscore_")
    return open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)


def _seed_concept(g, cid, code, n_practice, n_correct, cards_stability, weight=1.0):
    """One concept: `len(cards_stability)` cards (each a note tagged with the
    concept, reviewed once so it carries an R), plus `n_practice` PRACTICE novel
    attempts of which `n_correct` are correct."""
    con = g.db.con
    base = 1_700_000_000_000
    hour = 3_600_000
    con.execute("INSERT INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
                " VALUES(?,?,?,?,?)", (cid, code, f"C {code}", weight, 5.0))
    for j, s in enumerate(cards_stability):
        nid = cid * 100 + j
        con.execute("INSERT INTO notes(id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, f"g{nid}", 1, 0, -1, f" concept::{code} ", "f", "f", 0, 0, "{}"))
        con.execute("INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
                    "reps,lapses,left,odue,odid,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, nid, 1, 0, 0, -1, 2, 2, 0, 30, 2500, 1, 0, 0, 0, 0, 0,
                     f'{{"s": {s}, "d": 5.0}}'))
        con.execute("INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (base + nid * hour, nid, -1, 3, 10, 5, 2500, 5000, 1))
    for j in range(n_practice):
        item = 50_000_000_000 + cid * 1000 + j
        con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                    " VALUES(?,?,?,?,?,?)", (item, f"ni{item}", f"src-{cid}", 0, -1, 0))
        con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                    (item, cid))
        con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                    " VALUES(?,?,?,?,?)",
                    (base + 500 * hour + item, item, 1 if j < n_correct else 0, 5000, -1))
    g.commit()
    g.run_file("queries/01_open/rebuild_note_concepts.sql")
    g.commit()


# --------------------------------------------------------------------------- #
# 1 + 2. performance abstain/Wilson, memory bounds
# --------------------------------------------------------------------------- #
def test_performance_and_memory():
    g = _new_db()
    # concept 301: 10 practice attempts (>=8 -> scored), 2 cards for a real spread
    _seed_concept(g, 301, "S01", n_practice=10, n_correct=5,
                  cards_stability=[50.0, 20.0])
    # concept 302: 5 practice attempts (<8 -> abstain), 1 card
    _seed_concept(g, 302, "S02", n_practice=5, n_correct=3, cards_stability=[50.0])

    # --- performance abstains below the pre-registered line ------------------ #
    p_lo = scores.performance_score(g, 302)
    assert p_lo.abstained is True and p_lo.value is None, p_lo
    assert p_lo.n == 5 and p_lo.n < scores.PERFORMANCE_MIN_ATTEMPTS
    assert 0.0 <= p_lo.lo <= p_lo.hi <= 1.0

    # --- performance yields a Wilson interval at/above the line -------------- #
    p_hi = scores.performance_score(g, 301)
    assert p_hi.abstained is False and p_hi.value is not None, p_hi
    assert p_hi.n == 10
    assert 0.0 <= p_hi.lo < p_hi.hi <= 1.0, p_hi
    # 5/10 -> point 0.5, Wilson interval strictly inside (0,1)
    assert abs(p_hi.value - 0.5) < 1e-9
    assert p_hi.lo > 0.0 and p_hi.hi < 1.0
    # cross-check against the standalone Wilson primitive
    _, wl, wh = scores.wilson_interval(5, 10)
    assert abs(p_hi.lo - wl) < 1e-12 and abs(p_hi.hi - wh) < 1e-12

    # --- memory in [0,1] with lo <= value <= hi ------------------------------ #
    m = scores.memory_score(g, 301)
    assert m.abstained is False and m.value is not None, m
    assert 0.0 <= m.value <= 1.0, m
    assert m.lo <= m.value <= m.hi, m
    assert 0.0 <= m.lo and m.hi <= 1.0, m
    assert m.n == 2

    # memory abstains when the concept has no scored cards
    m_none = scores.memory_score(g, 999)
    assert m_none.abstained is True and m_none.value is None and m_none.n == 0
    print("OK  test_performance_and_memory  "
          f"(perf 0.5 CI=[{p_hi.lo:.3f},{p_hi.hi:.3f}], mem={m.value:.3f})")


# --------------------------------------------------------------------------- #
# 3. readiness widens as coverage drops
# --------------------------------------------------------------------------- #
def test_readiness_coverage_widens():
    g = _new_db()
    _seed_concept(g, 301, "S01", n_practice=10, n_correct=5, cards_stability=[50.0])
    _seed_concept(g, 302, "S02", n_practice=10, n_correct=5, cards_stability=[50.0])

    # full coverage: both outline concepts are scored (coverage = 1.0)
    full = scores.readiness_score(g, outline={301: 1.0, 302: 1.0})
    assert full.abstained is False and full.value is not None, full
    assert 0.0 <= full.lo <= full.value <= full.hi <= 100.0, full

    # sparse coverage: add two uncovered concepts -> coverage = 0.5 (== threshold,
    # still projects) but the interval must widen.
    sparse = scores.readiness_score(g, outline={301: 1.0, 302: 1.0,
                                                 901: 1.0, 902: 1.0})
    assert sparse.abstained is False, sparse
    assert (sparse.hi - sparse.lo) > (full.hi - full.lo), \
        f"coverage did not widen CI: full={full.hi-full.lo}, sparse={sparse.hi-sparse.lo}"

    # below the coverage line -> abstain
    starved = scores.readiness_score(g, outline={301: 1.0, 901: 1.0, 902: 1.0,
                                                  903: 1.0})
    assert starved.abstained is True and starved.value is None, starved

    # all_scores rollup shape
    alls = scores.all_scores(g, outline={301: 1.0, 302: 1.0})
    assert set(alls.keys()) == {"concepts", "readiness"}
    assert set(alls["concepts"].keys()) == {301, 302}
    assert set(alls["concepts"][301].keys()) == {"memory", "performance"}
    assert isinstance(alls["readiness"], scores.Score)
    print("OK  test_readiness_coverage_widens  "
          f"(full CI width={full.hi-full.lo:.1f}pp, sparse={sparse.hi-sparse.lo:.1f}pp)")


# --------------------------------------------------------------------------- #
# 4 + 5. brier / log_loss hand values; reliability bins sum to n
# --------------------------------------------------------------------------- #
def test_calibration_metrics():
    preds = [0.9, 0.8, 0.3, 0.6]
    outcomes = [1, 1, 0, 1]
    # brier = mean(0.01, 0.04, 0.09, 0.16) = 0.30/4 = 0.075
    b = calibration.brier(preds, outcomes)
    assert abs(b - 0.075) < 1e-12, b
    # log_loss = -mean(ln.9, ln.8, ln.7, ln.6) = 0.299001159...
    ll = calibration.log_loss(preds, outcomes)
    assert abs(ll - 0.2990011586691402) < 1e-9, ll

    # reliability bins sum to n
    bins = calibration.reliability_bins(preds, outcomes, nbins=5)
    assert sum(row["n"] for row in bins) == len(preds), bins
    # and on the seeded 400-review set too
    g, _tmp = calibration.seed_calibration_db()
    p2, o2 = calibration.extract_pairs(g)
    bins2 = calibration.reliability_bins(p2, o2, nbins=10)
    assert sum(row["n"] for row in bins2) == len(p2) == 400, len(p2)
    # calibrated by construction: every non-empty bin's observed frequency tracks
    # its predicted probability to within one review's granularity (1/40).
    for row in bins2:
        if row["n"]:
            assert abs(row["mean_pred"] - row["mean_obs"]) < 1.0 / calibration._REVIEWS_PER_BIN, row
    print(f"OK  test_calibration_metrics  (brier={b:.4f}, log_loss={ll:.4f})")


# --------------------------------------------------------------------------- #
# 6. leakage check: clean, then an injected holdout-boundary violation
# --------------------------------------------------------------------------- #
def test_leakage_clean_and_violation():
    tmp = tempfile.mkdtemp(prefix="gapleaktest_")
    g = open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)
    leakage_check.seed_clean(g)

    summary = dict(leakage_check.run_leakage_check(g))
    assert all(v == 0 for v in summary.values()), f"expected clean, got {summary}"

    # inject a holdout-boundary violation: reuse a PRACTICE source_id on a HOLDOUT
    # item, so one source now spans the holdout boundary (check b).
    con = g.db.con
    bad = 60_000_000_000
    con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                " VALUES(?,?,?,?,?,?)", (bad, "bad-item", "practice-src-201", 1, -1, 0))
    con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                (bad, 201))
    g.commit()

    summary2 = dict(leakage_check.run_leakage_check(g))
    assert summary2["b_source_spans_holdout"] > 0, summary2
    print("OK  test_leakage_clean_and_violation  "
          f"(injected b_source_spans_holdout={summary2['b_source_spans_holdout']})")


# --------------------------------------------------------------------------- #
# 7. runnable scripts exit 0
# --------------------------------------------------------------------------- #
def test_scripts_exit_zero():
    for rel in ("eval/calibration.py", "eval/leakage_check.py"):
        proc = subprocess.run([sys.executable, str(ROOT / rel)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{rel} exited {proc.returncode}\n{proc.stderr}"
    print("OK  test_scripts_exit_zero")


if __name__ == "__main__":
    test_performance_and_memory()
    test_readiness_coverage_widens()
    test_calibration_metrics()
    test_leakage_clean_and_violation()
    test_scripts_exit_zero()
    print("OK")
