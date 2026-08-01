#!/usr/bin/env python3
"""Plain-python tests for the paraphrase transfer test + the coverage map.

Asserts (then prints OK):
  1. paraphrase_gap surfaces a POSITIVE memory - performance gap on engineered
     data and FLAGS a high-memory / low-transfer concept; respects the abstain
     line (a concept with too few attempts earns no score).
  2. coverage_map returns EVERY outline topic, every coverage_pct in [0, 100],
     respects the abstain threshold, and shows 0% for an untouched topic and
     100% for a fully-covered one.
  3. Both runnable scripts (eval/paraphrase_test.py, coverage/coverage.py) exit 0.

Modules are loaded by FILE PATH (importlib) so the test is immune to the 'eval'
builtin and to a third-party 'coverage' package shadowing the local packages.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap.db import open_sidecar  # noqa: E402


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


para = _load("paraphrase_test_mod", "eval/paraphrase_test.py")
cov = _load("coverage_mod", "coverage/coverage.py")

OUTLINE = ROOT / "data" / "outline_lsat.json"


def _fresh_gapdb(prefix: str):
    tmp = tempfile.mkdtemp(prefix=prefix)
    return open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)


def test_paraphrase_gap():
    g = _fresh_gapdb("t_para_")
    # High memory / low transfer -> must be flagged, big positive gap.
    para.seed_probes(g, concept_id=9001, code="LR.1",
                     verbatim_correct=9, paraphrase_correct=4, n=10)
    # Clean transfer -> positive-but-small gap, not flagged.
    para.seed_probes(g, concept_id=9002, code="LR.2",
                     verbatim_correct=8, paraphrase_correct=7, n=10)
    # Too few attempts -> must abstain (no score), not counted in aggregate.
    para.seed_probes(g, concept_id=9003, code="RC.1",
                     verbatim_correct=5, paraphrase_correct=5, n=5)

    res = para.paraphrase_gap(g)
    by_code = {r["code"]: r for r in res["per_concept"]}

    # aggregate familiarity premium is strictly positive
    assert res["aggregate"]["mean_gap"] is not None
    assert res["aggregate"]["mean_gap"] > 0, res["aggregate"]

    # the high-memory / low-transfer concept is flagged; the clean one is not
    assert "LR.1" in res["aggregate"]["flagged_concepts"], res["aggregate"]
    assert by_code["LR.1"]["flagged"] is True
    assert by_code["LR.1"]["gap"] > 0.3, by_code["LR.1"]
    assert by_code["LR.2"]["flagged"] is False, by_code["LR.2"]

    # abstain line respected: the 5-attempt concept earns no score
    assert by_code["RC.1"]["has_score"] is False, by_code["RC.1"]
    assert by_code["RC.1"]["gap"] is None
    assert res["aggregate"]["scored_concepts"] == 2, res["aggregate"]

    # memory / performance numbers are the exact engineered means
    assert abs(by_code["LR.1"]["memory"] - 0.9) < 1e-9
    assert abs(by_code["LR.1"]["performance"] - 0.4) < 1e-9
    print(f"OK  test_paraphrase_gap  (mean familiarity premium="
          f"{res['aggregate']['mean_gap']:.3f}, flagged={res['aggregate']['flagged_concepts']})")


def test_coverage_map():
    g = _fresh_gapdb("t_cov_")
    threshold = cov.ABSTAIN_THRESHOLD
    # LR: every concept fully covered -> 100%.
    for i, code in enumerate(("LR.1", "LR.2", "LR.3", "LR.4")):
        cov._seed_concept(g, 8100 + i, code, n_attempts=threshold + 2, n_correct=threshold)
    # RC: one covered, one below the abstain line, one absent -> partial.
    cov._seed_concept(g, 8200, "RC.1", n_attempts=threshold + 1, n_correct=threshold)
    cov._seed_concept(g, 8201, "RC.2", n_attempts=threshold - 1, n_correct=1)  # below line
    # RC.3 intentionally NOT seeded (no gap.concepts row).
    # AR: nothing seeded at all -> 0%.

    cmap = cov.coverage_map(g, OUTLINE, min_attempts=threshold)

    outline = json.loads(OUTLINE.read_text())
    outline_topic_codes = {t["code"] for t in outline["topics"]}
    map_topic_codes = {t["code"] for t in cmap["topics"]}
    assert map_topic_codes == outline_topic_codes, (map_topic_codes, outline_topic_codes)

    topics = {t["code"]: t for t in cmap["topics"]}

    # every coverage_pct in [0, 100]
    for t in cmap["topics"]:
        assert 0.0 <= t["coverage_pct"] <= 100.0, t

    # fully-covered topic == 100%, untouched topic == 0%
    assert topics["LR"]["coverage_pct"] == 100.0, topics["LR"]
    assert topics["LR"]["covered_concepts"] == topics["LR"]["total_concepts"]
    assert topics["AR"]["coverage_pct"] == 0.0, topics["AR"]
    assert topics["AR"]["covered_concepts"] == 0
    assert topics["AR"]["mean_performance"] is None

    # abstain threshold respected on the partial topic: RC.1 covered, RC.2 (below
    # line) and RC.3 (absent) abstained -> 1 of 3.
    assert topics["RC"]["covered_concepts"] == 1, topics["RC"]
    assert topics["RC"]["abstain_count"] == 2, topics["RC"]
    assert abs(topics["RC"]["coverage_pct"] - 100.0 / 3.0) < 0.2, topics["RC"]

    # flat view: RC.2 exists but is not covered (below the line); RC.3 absent
    flat = {c["code"]: c for c in cmap["concepts"]}
    assert flat["RC.2"]["exists"] is True and flat["RC.2"]["covered"] is False
    assert flat["RC.3"]["exists"] is False and flat["RC.3"]["covered"] is False
    assert flat["LR.1"]["covered"] is True and flat["LR.1"]["performance"] is not None
    print(f"OK  test_coverage_map  (LR={topics['LR']['coverage_pct']:.0f}%, "
          f"RC={topics['RC']['coverage_pct']:.1f}%, AR={topics['AR']['coverage_pct']:.0f}%)")


def test_scripts_exit_zero():
    for rel in ("eval/paraphrase_test.py", "coverage/coverage.py"):
        proc = subprocess.run([sys.executable, str(ROOT / rel)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{rel} exited {proc.returncode}\n{proc.stderr}"
    print("OK  test_scripts_exit_zero  (eval/paraphrase_test.py, coverage/coverage.py)")


if __name__ == "__main__":
    test_paraphrase_gap()
    test_coverage_map()
    test_scripts_exit_zero()
    print("OK")
