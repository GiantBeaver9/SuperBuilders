"""Dashboard payload — the numbers the add-on's stats view renders.

**Live-safe by construction.** ``dashboard_payload`` may run *inside Anki*, whose
bundled SQLite has no math functions (`pow`/`sqrt`/`exp`/`ln` are all missing —
see ``docs/ENGINE.md`` §2). So this module computes every numeric that needs real
math in **Python**:

* card mastery (FSRS retrievability has a ``pow``) comes from :mod:`gap.mastery`;
* retirement state comes from :mod:`gap.retirement` (row-presence, no math);
* the latency **Pearson r** is computed here in Python, from a math-free series —
  the canonical ``latency_dissociation.sql`` correlation statement uses ``sqrt``
  and is therefore NEVER executed on this path.

The three endpoint SQL files that are already math-free — ``primary_crossover``
(descriptive contrast), ``terminal_novel_accuracy`` and ``throughput_cost`` — are
reused verbatim: they use only ``COUNT``/``SUM``/``AVG``/``NULLIF`` and ``*``//``/``
arithmetic, all of which Anki's SQLite has. Every division here is guarded — a
zero (or absent) denominator yields ``None``, never a raised error.

The returned dict is JSON-serializable (plain ``int``/``float``/``str``/``bool``/
``None``/``list``/``dict``); the sim writes it to ``sim/dashboard_data.json``.
"""
from __future__ import annotations

from typing import Any

from gap import mastery, retirement
from gap.db import split_statements

ABSTAIN_THRESHOLD: int = 8


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _statement(gapdb: Any, relpath: str, index: int) -> list[tuple]:
    """Execute exactly ONE statement of a committed SQL file (0-based) and return
    its rows, using only ``gapdb.execute`` (so it is identical on Anki's
    ``col.db`` and on the sim/test ``SqliteProxy`` — nothing here reaches into a
    backend-specific cursor). This lets a single math-free statement of a
    multi-statement file run without touching the file's math-bearing statements
    (e.g. the ``sqrt`` correlation in ``latency_dissociation.sql``). Results are
    read POSITIONALLY by the callers, matching each file's committed column order.
    """
    stmts = split_statements(gapdb.sql_text(relpath))
    return gapdb.execute(stmts[index])


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r in pure Python (no SQL, no ``sqrt`` executed on the live path —
    Python's own ``**0.5`` is used). Returns ``None`` for < 2 points or zero
    variance on either axis."""
    n = len(xs)
    if n < 2:
        return None
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); syy = sum(y * y for y in ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    num = n * sxy - sx * sy
    den_sq = (n * sxx - sx * sx) * (n * syy - sy * sy)
    if den_sq <= 0:
        return None
    return num / (den_sq ** 0.5)


def _f(v: Any) -> float | None:
    """Coerce a SQL numeric (possibly ``None``) to ``float`` | ``None``."""
    return None if v is None else float(v)


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #
def dashboard_payload(gapdb: Any, at_ms: int | None = None) -> dict:
    """Build the full, JSON-serializable dashboard payload.

    See the module docstring for the live-safety contract. ``at_ms`` (epoch ms)
    is the instant retrievability is evaluated at; ``None`` means "now".
    """
    generated_ms = mastery.now_ms() if at_ms is None else at_ms

    # -- Python-computed numerics (no SQL math) ----------------------------- #
    card_mastery = mastery.card_mastery_by_concept(gapdb, at_ms=generated_ms)
    practice_acc = mastery.novel_accuracy_by_concept(gapdb, holdout=False)
    retired = retirement.retired_concepts(gapdb)            # concept_id -> trigger

    # practice-attempt counts per concept (math-free COUNT; safe live)
    attempt_rows = gapdb.all(
        """
        SELECT nic.concept_id, COUNT(*) AS n
        FROM gap.novel_revlog nr
        JOIN gap.novel_items ni          ON ni.id = nr.item_id AND ni.is_holdout = 0
        JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
        GROUP BY nic.concept_id
        """
    )
    attempts = {int(cid): int(n) for cid, n in attempt_rows}

    # coverage of concept cards by reviewed cards, per concept (math-free)
    coverage_rows = gapdb.all(
        """
        SELECT nc.concept_id,
               COUNT(DISTINCT c.id) AS total_cards,
               COUNT(DISTINCT CASE WHEN r.cid IS NOT NULL THEN c.id END) AS reviewed_cards
        FROM gap.note_concepts nc
        JOIN main.notes n ON n.guid = nc.guid
        JOIN main.cards c ON c.nid = n.id
        LEFT JOIN main.revlog r ON r.cid = c.id
        GROUP BY nc.concept_id
        """
    )
    coverage = {}
    for cid, total, reviewed in coverage_rows:
        total = int(total or 0)
        coverage[int(cid)] = (100.0 * int(reviewed or 0) / total) if total else 0.0

    arm_of = {int(cid): arm for cid, arm in gapdb.all("SELECT concept_id, arm FROM gap.arms")}

    # -- per-concept rows ---------------------------------------------------- #
    concept_rows = gapdb.all(
        "SELECT id, code, name, weight FROM gap.concepts ORDER BY id"
    )
    concepts: list[dict] = []
    for cid, code, name, weight in concept_rows:
        cid = int(cid)
        n = attempts.get(cid, 0)
        scored = n >= ABSTAIN_THRESHOLD
        concepts.append({
            "concept_id": cid,
            "code": code,
            "name": name,
            "arm": arm_of.get(cid),
            "weight": _f(weight),
            "novel_attempts": n,
            "has_score": bool(scored),
            "performance": _f(practice_acc.get(cid)) if scored else None,
            "coverage_pct": round(coverage.get(cid, 0.0), 1),
            "card_mastery": _f(card_mastery.get(cid)),
            "retired": cid in retired,
            "retired_trigger": retired.get(cid),
        })

    # -- abstain block (reuses queries/05_discipline/abstain_rule.sql logic) - #
    below_accs = [
        practice_acc[cid] for cid, n in attempts.items()
        if 0 < n < ABSTAIN_THRESHOLD and cid in practice_acc
    ]
    above_accs = [
        practice_acc[cid] for cid, n in attempts.items()
        if n >= ABSTAIN_THRESHOLD and cid in practice_acc
    ]
    scored_n = sum(1 for c in concepts if c["has_score"])
    abstained_n = sum(1 for c in concepts if not c["has_score"])
    below_mean = (sum(below_accs) / len(below_accs)) if below_accs else None
    above_mean = (sum(above_accs) / len(above_accs)) if above_accs else None
    diff_pp = ((below_mean - above_mean) * 100.0
               if below_mean is not None and above_mean is not None else None)
    abstain = {
        "threshold": ABSTAIN_THRESHOLD,
        "scored": scored_n,
        "abstained": abstained_n,
        "below_line_mean_acc": below_mean,
        "above_line_mean_acc": above_mean,
        "diff_pp": diff_pp,
    }

    # -- arms block ---------------------------------------------------------- #
    arms = {a: {"concepts": 0, "retired": 0} for a in ("gate", "nogate", "vanilla")}
    for cid, arm in arm_of.items():
        if arm not in arms:
            arms[arm] = {"concepts": 0, "retired": 0}
        arms[arm]["concepts"] += 1
        if cid in retired:
            arms[arm]["retired"] += 1

    # -- endpoints ----------------------------------------------------------- #
    endpoints = {
        "crossover": _crossover(gapdb),
        "terminal": _terminal(gapdb),
        "throughput": _throughput(gapdb),
        "latency": _latency(gapdb),
    }

    return {
        "generated_ms": int(generated_ms),
        "concepts": concepts,
        "abstain": abstain,
        "arms": arms,
        "endpoints": endpoints,
    }


# --------------------------------------------------------------------------- #
# endpoint sub-builders (each reuses the canonical math-free endpoint SQL)
# --------------------------------------------------------------------------- #
def _crossover(gapdb: Any) -> list[dict]:
    """The descriptive crossover contrast — statement (b) of primary_crossover.sql
    (math-free: COUNT/SUM/NULLIF/`*`). Always emits both committed buckets so the
    sign flip is read at fixed positions.

    Committed column order of that statement:
      0 exposure_bucket, 1 n_gate, 2 n_nogate, 3 n_vanilla,
      4 acc_gate_pct, 5 acc_nogate_pct, 6 acc_vanilla_pct,
      7 diff_pp_gate_minus_nogate
    """
    rows = _statement(gapdb, "queries/04_endpoints/primary_crossover.sql", 1)
    by_bucket = {r[0]: r for r in rows}
    out: list[dict] = []
    for bucket in ("1-4", "5+"):
        r = by_bucket.get(bucket)
        if r is None:
            out.append({"bucket": bucket, "n_gate": 0, "n_nogate": 0,
                        "acc_gate_pct": None, "acc_nogate_pct": None, "diff_pp": None})
            continue
        out.append({
            "bucket": bucket,
            "n_gate": int(r[1] or 0),
            "n_nogate": int(r[2] or 0),
            "acc_gate_pct": _f(r[4]),
            "acc_nogate_pct": _f(r[5]),
            "diff_pp": _f(r[7]),
        })
    return out


def _terminal(gapdb: Any) -> dict:
    """Terminal held-out contrast — terminal_novel_accuracy.sql (single, math-free).

    Committed column order:
      0 endpoint, 1 gate_mean_pct, 2 nogate_mean_pct, 3 a_minus_b_pp,
      4 gate_n_attempts, 5 gate_n_concepts, 6 nogate_n_attempts, 7 nogate_n_concepts
    """
    rows = _statement(gapdb, "queries/04_endpoints/terminal_novel_accuracy.sql", 0)
    r = rows[0] if rows else None
    if r is None:
        return {"acc_gate_pct": None, "acc_nogate_pct": None, "diff_pp": None,
                "n_gate": 0, "n_nogate": 0}
    return {
        "acc_gate_pct": _f(r[1]),
        "acc_nogate_pct": _f(r[2]),
        "diff_pp": _f(r[3]),
        "n_gate": int(r[4]) if r[4] is not None else 0,
        "n_nogate": int(r[6]) if r[6] is not None else 0,
    }


def _throughput(gapdb: Any) -> dict:
    """Throughput cost — throughput_cost.sql (single, math-free). Counts persisted
    ``gap.retirements`` rows; ``pct_diff_A_vs_B`` is the A-vs-B retired-per-minute
    percent difference (predicted -25%...-35%).

    Committed column order:
      0 gate_retired, 1 gate_total_concepts, 2 gate_study_minutes,
      3 gate_retired_per_hour, 4 nogate_retired, ...,
      12 pct_diff_retired_per_min_A_vs_B
    """
    rows = _statement(gapdb, "queries/04_endpoints/throughput_cost.sql", 0)
    r = rows[0] if rows else None
    if r is None:
        return {"gate_retired": 0, "nogate_retired": 0, "pct_diff_A_vs_B": None}
    return {
        "gate_retired": int(r[0]) if r[0] is not None else 0,
        "nogate_retired": int(r[4]) if r[4] is not None else 0,
        "pct_diff_A_vs_B": _f(r[12]),
    }


def _latency(gapdb: Any) -> dict:
    """Latency dissociation. Runs ONLY statement (a) of latency_dissociation.sql —
    the math-free paired series (mean card time vs mean novel latency per exposure
    index) — and computes the Pearson r **in Python**. The canonical ``sqrt``
    correlation statement (b) is deliberately never executed here (live-safe).
    ``lockstep`` == r > 0.8 (the pre-registration's "claim dead" boundary).

    Committed column order of statement (a):
      0 exposure_index, 1 exposure_bucket, 2 mean_card_time, 3 n_card,
      4 mean_novel_time, 5 n_novel
    """
    rows = _statement(gapdb, "queries/04_endpoints/latency_dissociation.sql", 0)
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        c, v = r[2], r[4]
        if c is not None and v is not None:
            xs.append(float(c))
            ys.append(float(v))
    r_val = _pearson(xs, ys)
    return {"r": r_val, "lockstep": (r_val is not None and r_val > 0.8)}
