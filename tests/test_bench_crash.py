#!/usr/bin/env python3
"""Fast test for the bench + crash harness (plain asserts + print OK).

Keeps everything small so it runs in a few seconds:
  * builds a 2,000-card dataset and checks the engine ops it feeds still run,
  * checks the benchmark returns median/p95/worst timing dicts for every op,
  * runs a few crash iterations and asserts 0 corruptions + integrity ok,
  * runs both runnable scripts end-to-end (reduced N via env) and asserts exit 0.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bench import bench_engine, crash_test          # noqa: E402
from bench.seed_large import build_dataset, dataset_stats   # noqa: E402
from gap import mastery, queue, stats               # noqa: E402


def test_seed_builds_and_engine_runs() -> None:
    g = build_dataset(n_cards=2000, m_concepts=50)
    ds = dataset_stats(g)
    assert ds["cards"] == 2000, ds
    assert ds["concepts"] == 50, ds
    assert ds["revlog"] >= 2000, ds
    # every engine op the benchmark times must actually run on the dataset
    q = queue.points_at_stake(g)
    assert len(q) == 2000, len(q)
    assert {"card_id", "points", "concept_id"} <= set(q[0]), q[0]
    d = stats.dashboard_payload(g)
    assert len(d["concepts"]) == 50, d
    assert "abstain" in d and "endpoints" in d, d.keys()
    cm = mastery.card_mastery_by_concept(g)
    assert cm and all(0.0 <= r <= 1.0 for r in cm.values()), "R out of range"
    print("OK seed + engine ops")


def test_bench_returns_timing_dicts() -> None:
    bench = bench_engine.run_benchmarks(n_cards=2000, m_concepts=50, repetitions=3)
    assert set(bench["results"]) == {
        "queue", "dashboard_load", "dashboard_refresh", "mastery"}, bench["results"].keys()
    for name, s in bench["results"].items():
        for key in ("median", "p95", "worst", "best", "n"):
            assert key in s, f"{name} missing {key}"
        assert s["n"] == 3, s
        assert s["best"] <= s["median"] <= s["worst"], (name, s)
        assert s["p95"] <= s["worst"] and s["p95"] >= s["best"], (name, s)
        assert s["median"] > 0.0, (name, s)
    # report renders without error
    report = bench_engine.format_report(bench)
    assert "median (ms)" in report and "PRD" in report
    print("OK bench timing dicts")


def test_crash_zero_corruptions() -> None:
    summary = crash_test.run_crash_test(iterations=4, n_cards=1000, m_concepts=25)
    assert summary["iterations"] == 4, summary
    assert summary["corruptions"] == 0, summary["problems"]
    assert summary["collection_untouched_all"], "collection.anki2 modified"
    assert summary["journal_mode"].startswith("WAL"), summary["journal_mode"]
    # a crashed sidecar is still structurally sound + append-only intact
    assert not summary["problems"], summary["problems"]
    print(f"OK crash test: {summary['iterations'] - summary['corruptions']}"
          f"/{summary['iterations']} clean, "
          f"{summary['mid_write_rounds']} mid-write rounds")


def test_scripts_exit_zero() -> None:
    env = dict(os.environ)
    # Point RESULTS.md at a throwaway file so the test never clobbers the
    # canonical bench/RESULTS.md (which holds the real 50k / 20x numbers).
    import tempfile
    tmp_results = Path(tempfile.mkdtemp(prefix="results_")) / "RESULTS.md"
    env["RESULTS_MD"] = str(tmp_results)
    env.update(BENCH_N="800", BENCH_M="20", BENCH_R="3")
    r1 = subprocess.run([sys.executable, str(_REPO / "bench" / "bench_engine.py")],
                        env=env, capture_output=True, text=True)
    assert r1.returncode == 0, f"bench_engine.py exit {r1.returncode}\n{r1.stderr}"

    env.update(CRASH_ITERS="3", CRASH_N="800", CRASH_M="20")
    r2 = subprocess.run([sys.executable, str(_REPO / "bench" / "crash_test.py")],
                        env=env, capture_output=True, text=True)
    assert r2.returncode == 0, f"crash_test.py exit {r2.returncode}\n{r2.stderr}"
    assert "PASS" in r2.stdout, r2.stdout
    print("OK both scripts exit 0")


if __name__ == "__main__":
    test_seed_builds_and_engine_runs()
    test_bench_returns_timing_dicts()
    test_crash_zero_corruptions()
    test_scripts_exit_zero()
    print("\nOK")
