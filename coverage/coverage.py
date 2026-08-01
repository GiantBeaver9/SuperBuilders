#!/usr/bin/env python3
"""Coverage map — exam readiness per outline TOPIC, not just per concept.

The dashboard already scores individual concepts (``gap/stats.py``). This rolls
those concepts up under an official exam OUTLINE (``data/outline_lsat.json``) so a
student sees, per section: how many concepts the outline lists, how many are
actually COVERED (have enough novel-attempt data to earn a Performance score),
what fraction that is, and the mean performance where scored. A section a student
has never touched shows 0% honestly, instead of being averaged away.

TIE TO THE ENGINE
  Each outline concept ``code`` is matched to a ``gap.concepts.code`` row (authored
  on notes as ``concept::<code>``). A concept counts as COVERED when it has at
  least the abstain threshold of PRACTICE novel attempts (``ABSTAIN_THRESHOLD`` in
  ``gap/stats.py`` — the same line the per-concept dashboard abstains at). Outline
  concepts with no matching ``gap.concepts`` row, or with too few attempts, are
  abstained (counted, never scored). Math-light: counts and means only, all in
  Python over math-free reads.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap.db import open_sidecar  # noqa: E402
from gap import mastery, novel  # noqa: E402
from gap.stats import ABSTAIN_THRESHOLD  # noqa: E402

DEFAULT_OUTLINE = ROOT / "data" / "outline_lsat.json"


def load_outline(outline_path: str | Path) -> dict:
    """Read and lightly validate an outline JSON (topics -> concepts, both with
    codes and weights)."""
    data = json.loads(Path(outline_path).read_text())
    if "topics" not in data or not isinstance(data["topics"], list):
        raise ValueError("outline missing a 'topics' list")
    return data


def coverage_map(gapdb: Any, outline_path: str | Path = DEFAULT_OUTLINE,
                 min_attempts: int = ABSTAIN_THRESHOLD) -> dict:
    """Roll concept coverage up to each outline topic.

    Returns a dict with:

      * ``exam`` / ``outline_version`` — echoed from the outline
      * ``threshold`` — the abstain line used (``min_attempts``)
      * ``topics`` — per topic: ``total_concepts``, ``covered_concepts``,
        ``coverage_pct`` (in [0, 100]), ``mean_performance`` (over scored
        concepts, or ``None``), ``abstain_count`` (total - covered)
      * ``concepts`` — a FLAT per-concept view (topic code, concept code/name/
        weight, whether it exists in gap.concepts, attempt count, covered flag,
        performance or ``None``)

    Concept ``code`` ties to ``gap.concepts.code``; coverage uses PRACTICE novel
    attempts (``is_holdout=0``), matching the dashboard's abstain rule.
    """
    outline = load_outline(outline_path)

    # code -> concept_id, from gap.concepts (the authored, tag-derived truth)
    code_to_id = {code: int(cid) for cid, code in
                  gapdb.all("SELECT id, code FROM gap.concepts")}
    # concept_id -> practice novel attempt count / mean accuracy
    attempts = novel.attempt_counts(gapdb, holdout=False)
    perf = mastery.novel_accuracy_by_concept(gapdb, holdout=False)

    topics_out: list[dict] = []
    flat: list[dict] = []
    for topic in outline["topics"]:
        concepts = topic.get("concepts", [])
        covered = 0
        scored_perfs: list[float] = []
        for c in concepts:
            code = c["code"]
            cid = code_to_id.get(code)
            n = attempts.get(cid, 0) if cid is not None else 0
            is_covered = cid is not None and n >= min_attempts
            p = perf.get(cid) if (is_covered and cid is not None) else None
            if is_covered:
                covered += 1
                if p is not None:
                    scored_perfs.append(p)
            flat.append({
                "topic_code": topic["code"],
                "topic_name": topic["name"],
                "code": code,
                "name": c.get("name"),
                "weight": c.get("weight"),
                "concept_id": cid,
                "exists": cid is not None,
                "novel_attempts": n,
                "covered": is_covered,
                "performance": p,
            })
        total = len(concepts)
        coverage_pct = (100.0 * covered / total) if total else 0.0
        mean_perf = (sum(scored_perfs) / len(scored_perfs)) if scored_perfs else None
        topics_out.append({
            "code": topic["code"],
            "name": topic["name"],
            "weight": topic.get("weight"),
            "total_concepts": total,
            "covered_concepts": covered,
            "coverage_pct": round(coverage_pct, 1),
            "mean_performance": mean_perf,
            "abstain_count": total - covered,
        })

    return {
        "exam": outline.get("exam"),
        "outline_version": outline.get("outline_version"),
        "threshold": min_attempts,
        "topics": topics_out,
        "concepts": flat,
    }


# --------------------------------------------------------------------------- #
# demo seed: a realistic mix so the printed map has covered + abstained topics
# --------------------------------------------------------------------------- #
def _seed_concept(gapdb: Any, cid: int, code: str, n_attempts: int, n_correct: int,
                  base_ms: int = 1_700_000_000_000) -> None:
    """Seed one gap.concept + ``n_attempts`` PRACTICE novel attempts (``n_correct``
    of them correct). Deterministic; no card/revlog needed for the coverage map."""
    con = gapdb.db.con
    con.execute(
        "INSERT OR IGNORE INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
        " VALUES(?,?,?,?,?)", (cid, code, f"Concept {code}", 1.0, 5.0))
    if n_attempts <= 0:
        gapdb.commit()
        return
    vals = [1] * n_correct + [0] * (n_attempts - n_correct)
    cbase = base_ms + cid * 1_000_000
    for j in range(n_attempts):
        item_id = cbase + j
        con.execute(
            "INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
            " VALUES(?,?,?,?,?,?)",
            (item_id, f"cov{code}:{j}", f"cov:{code}", 0, -1, item_id))
        con.execute(
            "INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
            (item_id, cid))
        con.execute(
            "INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn) VALUES(?,?,?,?,?)",
            (item_id + 1, item_id, vals[j], 6000, -1))
    gapdb.commit()


# (code, attempts, correct). LR fully covered; RC partially; AR untouched (0%).
_DEMO = [
    ("LR.1", 10, 8), ("LR.2", 10, 7), ("LR.3", 12, 9), ("LR.4", 9, 6),
    ("RC.1", 10, 7), ("RC.2", 3, 2), ("RC.3", 0, 0),
    # AR.1 / AR.2 / AR.3 intentionally left with NO gap.concepts row -> abstained.
]


def _seed_demo(gapdb: Any) -> None:
    for i, (code, n, k) in enumerate(_DEMO):
        _seed_concept(gapdb, 5000 + i, code, n, k)


def _fmt(v, spec=".2f"):
    return "  --" if v is None else format(v, spec)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="coverage_")
    g = open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)
    _seed_demo(g)

    cmap = coverage_map(g, DEFAULT_OUTLINE)

    print("=" * 74)
    print(f"COVERAGE MAP  —  {cmap['exam']}  (outline {cmap['outline_version']})")
    print(f"outline: {DEFAULT_OUTLINE}")
    print(f"covered = concept has >= {cmap['threshold']} practice novel attempts")
    print("=" * 74)
    print(f"{'topic':<6} {'section':<34} {'cov/total':>10} {'cov%':>6} "
          f"{'meanPerf':>9} {'abst':>5}")
    print("-" * 74)
    for t in cmap["topics"]:
        print(f"{t['code']:<6} {t['name'][:34]:<34} "
              f"{str(t['covered_concepts'])+'/'+str(t['total_concepts']):>10} "
              f"{t['coverage_pct']:>5.0f}% {_fmt(t['mean_performance']):>9} "
              f"{t['abstain_count']:>5}")
    print("-" * 74)
    print("per-concept:")
    print(f"  {'code':<6} {'exists':>6} {'attempts':>8} {'covered':>7} {'perf':>6}")
    for c in cmap["concepts"]:
        print(f"  {c['code']:<6} {str(c['exists']):>6} {c['novel_attempts']:>8} "
              f"{str(c['covered']):>7} {_fmt(c['performance']):>6}")
    print("=" * 74)
    print("CAVEAT: SIMULATED coverage over seeded data to demonstrate the "
          "roll-up. The outline")
    print("is ILLUSTRATIVE and swappable (data/outline_lsat.json); "
          "coverage_map() itself is pure")
    print("measurement over real gap.db data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
