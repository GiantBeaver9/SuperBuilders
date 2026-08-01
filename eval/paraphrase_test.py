#!/usr/bin/env python3
"""Paraphrase / reword transfer test — MEMORY vs PERFORMANCE, made rerunnable.

This operationalizes the pre-registration's central decoupling thesis
(``PREREGISTRATION.md``, POV-1): recognizing the *trained wording* of a concept
(MEMORY / retrieval strength) is not the same as being able to answer a *reworded,
novel phrasing* of the SAME concept (PERFORMANCE / transfer). Every tool scores
the first and quietly implies the second; the gap between them is exactly the
failure this product exists to catch.

HOW IT IS MODELED (schema-honest, no new tables)
  For each concept we seed two kinds of ``gap.novel_items`` (see ``schema/gap.sql``):

    * a VERBATIM probe  — the trained wording restated. Its accuracy is the
      MEMORY score. ``source_id`` is prefixed ``verbatim:``; ``is_holdout = 0``
      (practice — this is the "recognize what you studied" signal).
    * a PARAPHRASE probe — the same concept in a distinct, reworded item
      (different ``guid``, same concept link, ``source_id`` prefixed
      ``paraphrase:``, ``is_holdout = 1`` so a transfer probe is terminal and is
      never surfaced during study). Its accuracy is the PERFORMANCE score; the
      harder wording is what makes it reflect transfer rather than familiarity.

  Both are ordinary novel items — nothing here writes ``main.*`` and nothing
  re-uses a card. The verbatim/paraphrase distinction lives entirely in the
  ``source_id`` prefix, so this test is orthogonal to the rest of the pipeline.

WHAT IT REPORTS
  Per concept: memory, performance, and the MEMORY - PERFORMANCE gap (the
  "familiarity premium"). Aggregate: mean memory, mean performance, mean gap.
  It FLAGS concepts where memory is high but paraphrase performance is low — the
  exact "looks learned, isn't transferring" case. A stated data cutoff filters
  attempts by timestamp, matching the pre-registration's cutoff discipline.

HONEST CAVEAT
  Run standalone, this seeds SIMULATED data engineered to the thesis shape to
  demonstrate the measurement. It is NOT an empirical result. ``paraphrase_gap``
  itself is pure measurement and runs identically over real seeded ``gap.db``
  data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap.db import open_sidecar  # noqa: E402
from gap.stats import ABSTAIN_THRESHOLD  # noqa: E402  (reuse the one abstain line)

# ---- probe conventions ------------------------------------------------------ #
VERBATIM_PREFIX = "verbatim:"      # trained wording restated -> MEMORY
PARAPHRASE_PREFIX = "paraphrase:"  # reworded, same concept   -> PERFORMANCE

# ---- flagging thresholds (the "looks learned, isn't transferring" box) ------ #
HIGH_MEMORY = 0.80        # memory at/above this reads as "well trained"
LOW_TRANSFER = 0.60       # performance below this reads as "not transferring"

# ---- stated data cutoff ----------------------------------------------------- #
# Committed BEFORE inspecting outcomes, per the pre-registration's data
# discipline. Attempts with revlog id (epoch ms) after this are excluded.
CUTOFF_MS = 1_800_000_000_000  # 2027-01-15T08:00:00Z, comfortably after the seed


def _iso(ms: int) -> str:
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# the measurement (pure; runs over any seeded gap.db)
# --------------------------------------------------------------------------- #
def paraphrase_gap(gapdb: Any, cutoff_ms: int = CUTOFF_MS,
                   min_attempts: int = ABSTAIN_THRESHOLD,
                   high_memory: float = HIGH_MEMORY,
                   low_transfer: float = LOW_TRANSFER) -> dict:
    """MEMORY vs PERFORMANCE per concept, plus an aggregate familiarity premium.

    For each concept that carries BOTH verbatim and paraphrase probes with at
    least ``min_attempts`` scored attempts (the abstain rule, applied to each
    probe type independently), report:

      * ``memory``      — mean ``correct`` on ``verbatim:`` probes
      * ``performance`` — mean ``correct`` on ``paraphrase:`` probes
      * ``gap``         — memory - performance (the "familiarity premium")
      * ``flagged``     — memory >= ``high_memory`` and performance < ``low_transfer``

    Only attempts with ``novel_revlog.id <= cutoff_ms`` count. Math-free SQL; the
    means and the gap are computed in Python. Returns a dict with ``per_concept``
    (list, ordered by concept code), an ``aggregate`` block, and the settings
    used (``cutoff_ms``, ``min_attempts``, thresholds).
    """
    rows = gapdb.all(
        """
        SELECT nic.concept_id, c.code, ni.source_id, nr.correct
        FROM gap.novel_revlog nr
        JOIN gap.novel_items ni          ON ni.id = nr.item_id
        JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
        JOIN gap.concepts c              ON c.id = nic.concept_id
        WHERE nr.id <= ?
        """,
        cutoff_ms,
    )

    # concept_id -> {"code": str, "v": [n_correct, n], "p": [n_correct, n]}
    acc: dict[int, dict] = {}
    for cid, code, source_id, correct in rows:
        sid = source_id or ""
        if sid.startswith(VERBATIM_PREFIX):
            kind = "v"
        elif sid.startswith(PARAPHRASE_PREFIX):
            kind = "p"
        else:
            continue  # ordinary practice/holdout item, not a paraphrase probe
        slot = acc.setdefault(int(cid), {"code": code, "v": [0, 0], "p": [0, 0]})
        slot[kind][0] += int(correct)
        slot[kind][1] += 1

    per_concept: list[dict] = []
    for cid, slot in acc.items():
        vk, vn = slot["v"]
        pk, pn = slot["p"]
        scored = vn >= min_attempts and pn >= min_attempts
        memory = (vk / vn) if vn else None
        performance = (pk / pn) if pn else None
        gap = (memory - performance) if scored else None
        flagged = bool(scored and memory >= high_memory and performance < low_transfer)
        per_concept.append({
            "concept_id": cid,
            "code": slot["code"],
            "verbatim_attempts": vn,
            "paraphrase_attempts": pn,
            "has_score": scored,
            "memory": memory,
            "performance": performance,
            "gap": gap,
            "flagged": flagged,
        })
    per_concept.sort(key=lambda r: r["code"])

    scored_rows = [r for r in per_concept if r["has_score"]]
    n = len(scored_rows)
    aggregate = {
        "scored_concepts": n,
        "abstained_concepts": len(per_concept) - n,
        "mean_memory": (sum(r["memory"] for r in scored_rows) / n) if n else None,
        "mean_performance": (sum(r["performance"] for r in scored_rows) / n) if n else None,
        "mean_gap": (sum(r["gap"] for r in scored_rows) / n) if n else None,
        "flagged_concepts": [r["code"] for r in scored_rows if r["flagged"]],
    }
    return {
        "per_concept": per_concept,
        "aggregate": aggregate,
        "cutoff_ms": cutoff_ms,
        "min_attempts": min_attempts,
        "high_memory": high_memory,
        "low_transfer": low_transfer,
    }


# --------------------------------------------------------------------------- #
# probe seeding helper (reusable by tests)
# --------------------------------------------------------------------------- #
def seed_probes(gapdb: Any, concept_id: int, code: str,
                verbatim_correct: int, paraphrase_correct: int,
                n: int = 10, base_ms: int = 1_700_000_000_000,
                ensure_concept: bool = True) -> None:
    """Seed ``n`` verbatim + ``n`` paraphrase probes for one concept.

    ``verbatim_correct`` / ``paraphrase_correct`` are the number correct out of
    ``n`` (means are exact and deterministic — no RNG). Verbatim probes are
    practice (``is_holdout=0``), paraphrase probes are terminal (``is_holdout=1``).
    All attempts land well before ``CUTOFF_MS``.
    """
    con = gapdb.db.con
    if ensure_concept:
        con.execute(
            "INSERT OR IGNORE INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
            " VALUES(?,?,?,?,?)",
            (concept_id, code, f"Concept {code}", 1.0, 5.0))
    cbase = base_ms + concept_id * 1_000_000
    for kind, prefix, holdout, k in (
        ("v", VERBATIM_PREFIX, 0, verbatim_correct),
        ("p", PARAPHRASE_PREFIX, 1, paraphrase_correct),
    ):
        vals = [1] * k + [0] * (n - k)  # exact count; deterministic order
        for j in range(n):
            item_id = cbase + (0 if kind == "v" else 500_000) + j
            rid = item_id + 1
            con.execute(
                "INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                " VALUES(?,?,?,?,?,?)",
                (item_id, f"{prefix}{code}:{j}", f"{prefix}{code}", holdout, -1, item_id))
            con.execute(
                "INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                (item_id, concept_id))
            con.execute(
                "INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn) VALUES(?,?,?,?,?)",
                (rid, item_id, vals[j], 6000, -1))
    gapdb.commit()


# --------------------------------------------------------------------------- #
# demo seed: engineered to the thesis shape
# --------------------------------------------------------------------------- #
# (code, verbatim_correct/10, paraphrase_correct/10). Two concepts show the
# textbook failure: high MEMORY, low PERFORMANCE. Others transfer cleanly.
_DEMO = [
    ("LR.1", 9, 5),   # 0.90 memory / 0.50 perf -> gap +0.40, FLAGGED
    ("LR.4", 10, 4),  # 1.00 memory / 0.40 perf -> gap +0.60, FLAGGED (classic)
    ("LR.2", 8, 7),   # 0.80 / 0.70 -> gap +0.10
    ("RC.1", 7, 7),   # 0.70 / 0.70 -> gap  0.00 (transfer intact)
    ("AR.1", 9, 8),   # 0.90 / 0.80 -> gap +0.10 (well-learned, transfers)
]


def _seed_demo(gapdb: Any) -> None:
    for i, (code, vk, pk) in enumerate(_DEMO):
        seed_probes(gapdb, concept_id=4000 + i, code=code,
                    verbatim_correct=vk, paraphrase_correct=pk)


def _fmt(v, spec=".2f"):
    return "  --" if v is None else format(v, spec)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="paraphrase_")
    g = open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)
    _seed_demo(g)

    result = paraphrase_gap(g)
    agg = result["aggregate"]

    print("=" * 74)
    print("PARAPHRASE / REWORD TRANSFER TEST  —  MEMORY vs PERFORMANCE")
    print("=" * 74)
    print(f"data cutoff:      {_iso(result['cutoff_ms'])}  (id <= {result['cutoff_ms']})")
    print(f"abstain / probe:  >= {result['min_attempts']} attempts per probe type")
    print(f"flag box:         memory >= {result['high_memory']:.2f} "
          f"AND performance < {result['low_transfer']:.2f}")
    print("-" * 74)
    print(f"{'concept':<8} {'v/p n':>7} {'MEMORY':>8} {'PERF':>8} "
          f"{'GAP':>8}  flag")
    print("-" * 74)
    for r in result["per_concept"]:
        flag = "  <-- HIGH MEMORY / LOW TRANSFER" if r["flagged"] else ""
        print(f"{r['code']:<8} {str(r['verbatim_attempts'])+'/'+str(r['paraphrase_attempts']):>7} "
              f"{_fmt(r['memory']):>8} {_fmt(r['performance']):>8} "
              f"{_fmt(r['gap']):>8}{flag}")
    print("-" * 74)
    print(f"AGGREGATE  scored={agg['scored_concepts']} "
          f"abstained={agg['abstained_concepts']}")
    print(f"  mean memory      = {_fmt(agg['mean_memory'])}")
    print(f"  mean performance = {_fmt(agg['mean_performance'])}")
    print(f"  familiarity premium (mean memory - performance) = "
          f"{_fmt(agg['mean_gap'])}")
    print(f"  FLAGGED (high memory, low transfer): "
          f"{', '.join(agg['flagged_concepts']) or 'none'}")
    print("=" * 74)
    print("CAVEAT: SIMULATED data engineered to the decoupling thesis to "
          "demonstrate the")
    print("measurement — NOT an empirical result. paraphrase_gap() itself is "
          "pure measurement")
    print("and runs identically over real seeded gap.db data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
