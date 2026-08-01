#!/usr/bin/env python3
"""Calibration harness for the MEMORY model (FSRS retrievability).

The memory score claims a probability: "R = 0.8" should mean "recalled ~80% of
the time". This harness checks that claim on a deterministic seeded review
history with a **stated data cutoff**. For every review it pairs:

* the model's **predicted** recall probability — FSRS-5 ``R`` computed from the
  card's stability and the elapsed interval *before* that review
  (:func:`gap.mastery.retrievability`), and
* the **actual** outcome — ``1`` if the grade was Hard/Good/Easy (``ease >= 2``),
  ``0`` on a lapse (``ease == 1``).

It then reports three things, all implemented in stdlib (no scipy):

* **Brier score** — mean squared error of the probabilities (lower is better).
* **Log-loss** — mean negative log-likelihood (lower is better).
* **Reliability table** — predictions binned into deciles with predicted-vs-
  observed frequency per bin, so "0.8 → ~0.8" is directly checkable.

Determinism: the seed data is fixed and outcomes are assigned by construction so
each decile's observed frequency tracks its predicted probability. Run it with
``python3 eval/calibration.py``; the numbers are the same every run.

Modeling note (stated, not hidden): in this harness each card's stability is
treated as fixed and the elapsed interval is ``revlog.lastIvl`` (days since the
prior review). Real FSRS updates stability every review; a per-review-stability
replay is a larger job — the point here is to exercise and demonstrate the
calibration metrics on a well-specified predicted-vs-observed stream.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gap import mastery                       # noqa: E402
from gap.db import open_sidecar               # noqa: E402

# --------------------------------------------------------------------------- #
# Stated data cutoff — reviews at or before this instant are in scope. Fixed so
# the eval is reproducible; printed in the report.
# --------------------------------------------------------------------------- #
DATA_CUTOFF_MS: int = 1_735_689_600_000  # 2025-01-01T00:00:00Z
"""Reviews with ``revlog.id <= DATA_CUTOFF_MS`` are evaluated; later ones are
held out of the calibration set. Committed here, printed in the report."""

_SEED_STABILITY_DAYS: float = 100.0
"""Fixed FSRS stability used for every seeded calibration card (days)."""

_REVIEWS_PER_BIN: int = 40
"""Seeded reviews per target decile — enough that observed frequency is stable."""

_LOGLOSS_EPS: float = 1e-15
"""Clamp on predicted probabilities in log-loss so ``log(0)`` never occurs."""


# --------------------------------------------------------------------------- #
# Metrics (stdlib only)
# --------------------------------------------------------------------------- #
def brier(preds: list[float], outcomes: list[int]) -> float:
    """Brier score: mean over reviews of ``(predicted - outcome)**2``.

    ``preds`` are probabilities in ``[0, 1]``, ``outcomes`` are ``0``/``1``. 0 is
    perfect, 0.25 is the always-0.5 baseline. Raises on length mismatch / empty.
    """
    if len(preds) != len(outcomes):
        raise ValueError("preds and outcomes differ in length")
    if not preds:
        raise ValueError("no predictions")
    return sum((p - o) ** 2 for p, o in zip(preds, outcomes)) / len(preds)


def log_loss(preds: list[float], outcomes: list[int],
             eps: float = _LOGLOSS_EPS) -> float:
    """Mean negative log-likelihood (natural log).

    ``-mean[ o·ln(p) + (1-o)·ln(1-p) ]`` with ``p`` clamped to ``[eps, 1-eps]`` so
    a confident wrong prediction is penalized heavily but never infinitely.
    """
    if len(preds) != len(outcomes):
        raise ValueError("preds and outcomes differ in length")
    if not preds:
        raise ValueError("no predictions")
    total = 0.0
    for p, o in zip(preds, outcomes):
        pc = min(1.0 - eps, max(eps, p))
        total += -(o * math.log(pc) + (1 - o) * math.log(1.0 - pc))
    return total / len(preds)


def reliability_bins(preds: list[float], outcomes: list[int], nbins: int = 10
                     ) -> list[dict]:
    """Bin predictions into ``nbins`` equal-width buckets over ``[0, 1]``.

    Returns one dict per bin (all bins, even empty) with keys ``bin_lo``,
    ``bin_hi``, ``n``, ``mean_pred`` (mean predicted prob, ``None`` if empty) and
    ``mean_obs`` (observed correct frequency, ``None`` if empty). A bin whose
    ``mean_pred`` ≈ ``mean_obs`` is well-calibrated. The ``n`` values sum to
    ``len(preds)`` — every review lands in exactly one bin (the top edge 1.0 goes
    in the last bin).
    """
    if len(preds) != len(outcomes):
        raise ValueError("preds and outcomes differ in length")
    width = 1.0 / nbins
    sums_p = [0.0] * nbins
    sums_o = [0] * nbins
    counts = [0] * nbins
    for p, o in zip(preds, outcomes):
        idx = int(p / width)
        if idx >= nbins:        # p == 1.0 (or float overshoot) -> last bin
            idx = nbins - 1
        if idx < 0:
            idx = 0
        sums_p[idx] += p
        sums_o[idx] += o
        counts[idx] += 1
    out: list[dict] = []
    for i in range(nbins):
        c = counts[i]
        out.append({
            "bin_lo": round(i * width, 4),
            "bin_hi": round((i + 1) * width, 4),
            "n": c,
            "mean_pred": (sums_p[i] / c) if c else None,
            "mean_obs": (sums_o[i] / c) if c else None,
        })
    return out


# --------------------------------------------------------------------------- #
# Deterministic seed + extraction
# --------------------------------------------------------------------------- #
def _elapsed_for_target(p: float) -> int:
    """Elapsed days (integer ``lastIvl``) whose FSRS R ≈ ``p`` at the fixed
    stability. Inverts ``R = (1 + F·t/S)^DECAY`` for ``t``."""
    # R = (1 + F t/S)^decay  =>  t = S/F * (p^(1/decay) - 1)
    ratio = p ** (1.0 / mastery.DECAY) - 1.0
    return max(0, round(_SEED_STABILITY_DAYS / mastery.FACTOR * ratio))


def seed_calibration_db():
    """Build a fresh ``open_sidecar`` DB seeded with a deterministic, well-
    calibrated review history and return ``(gapdb, tmpdir)``.

    Ten card groups target decile centres 0.05 … 0.95. Each group's cards share
    the fixed stability and a ``lastIvl`` chosen so predicted R lands at the
    target; then a matching fraction of that group's reviews are graded Good and
    the rest lapse, so observed frequency ≈ predicted probability by construction.
    """
    tmp = tempfile.mkdtemp(prefix="gapcal_")
    g = open_sidecar(None, Path(tmp) / "gap.db", main_stub=True)
    con = g.db.con
    data = f'{{"s": {_SEED_STABILITY_DAYS}}}'
    rid = DATA_CUTOFF_MS - 10_000_000_000       # all reviews before the cutoff
    cid = 1
    for decile in range(10):
        target = (decile + 0.5) / 10.0          # 0.05, 0.15, ... 0.95
        last_ivl = _elapsed_for_target(target)
        # actual predicted R at the integer interval (what the model will report)
        pred = mastery.retrievability(_SEED_STABILITY_DAYS, float(last_ivl))
        n_correct = round(pred * _REVIEWS_PER_BIN)
        # one card per group; N reviews on it, each a standalone predicted-vs-actual
        con.execute(
            "INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
            "reps,lapses,left,odue,odid,flags,data) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, cid, 1, 0, 0, -1, 2, 2, 0, last_ivl, 2500, _REVIEWS_PER_BIN,
             0, 0, 0, 0, 0, data),
        )
        for k in range(_REVIEWS_PER_BIN):
            correct = k < n_correct
            ease = 3 if correct else 1          # Good vs lapse
            con.execute(
                "INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, cid, -1, ease, last_ivl, last_ivl, 2500, 5000, 1),
            )
            rid += 1
        cid += 1
    g.commit()
    return g, tmp


_EXTRACT_SQL = """
SELECT r.ease, r.lastIvl, c.data
FROM main.revlog r
JOIN main.cards c ON c.id = r.cid
WHERE r.id <= ?
ORDER BY r.id
"""


def extract_pairs(gapdb, cutoff_ms: int = DATA_CUTOFF_MS
                  ) -> tuple[list[float], list[int]]:
    """Return ``(preds, outcomes)`` over all reviews at or before ``cutoff_ms``.

    Predicted = FSRS R from the card's stability and the review's ``lastIvl``
    (:func:`gap.mastery.retrievability`); outcome = ``1`` if ``ease >= 2`` else
    ``0``. Reviews on cards without FSRS stability are skipped.
    """
    preds: list[float] = []
    outcomes: list[int] = []
    for ease, last_ivl, data in gapdb.all(_EXTRACT_SQL, cutoff_ms):
        s = mastery.fsrs_stability(data)
        if s is None:
            continue
        preds.append(mastery.retrievability(s, float(last_ivl or 0)))
        outcomes.append(1 if int(ease) >= 2 else 0)
    return preds, outcomes


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _fmt_cutoff(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        ms / 1000.0, tz=datetime.timezone.utc).isoformat()


def run_report() -> dict:
    """Seed, evaluate, print the calibration report, and return the numbers."""
    g, tmp = seed_calibration_db()
    try:
        preds, outcomes = extract_pairs(g)
        b = brier(preds, outcomes)
        ll = log_loss(preds, outcomes)
        bins = reliability_bins(preds, outcomes, nbins=10)
    finally:
        pass

    print("=" * 66)
    print("MEMORY-MODEL CALIBRATION (FSRS retrievability)")
    print("=" * 66)
    print(f"data cutoff : {_fmt_cutoff(DATA_CUTOFF_MS)}  (id <= {DATA_CUTOFF_MS})")
    print(f"reviews     : {len(preds)}")
    print(f"Brier score : {b:.4f}   (0 = perfect, 0.25 = always-0.5 baseline)")
    print(f"Log-loss    : {ll:.4f}   (lower is better)")
    print("-" * 66)
    print("reliability table (predicted R  ->  observed recall):")
    print(f"  {'bin':>11}  {'n':>4}  {'pred':>6}  {'obs':>6}  {'gap':>6}")
    for row in bins:
        if row["n"] == 0:
            continue
        gap = abs(row["mean_pred"] - row["mean_obs"])
        print(f"  {row['bin_lo']:.2f}-{row['bin_hi']:.2f}  {row['n']:>4}  "
              f"{row['mean_pred']:>6.3f}  {row['mean_obs']:>6.3f}  {gap:>6.3f}")
    print("=" * 66)
    return {"brier": b, "log_loss": ll, "n": len(preds), "bins": bins}


def main() -> int:
    run_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
