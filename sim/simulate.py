#!/usr/bin/env python3
"""Seeded, deterministic end-to-end simulation of the novel-item-gate study.

Runs entirely on the OFFLINE path — a plain ``sqlite3`` connection opened via
``gap.db.open_sidecar(main_stub=True)``, whose SQLite HAS the math functions the
analysis SQL needs. Nothing here goes through Anki. The run is fully deterministic
(fixed RNG seed; the outcome counts are engineered proportions, not random draws,
so re-running yields byte-identical results).

WHAT IT BUILDS
  3 students x 40 concepts = 120 student x concept units. Each concept is assigned
  an arm (A ``gate`` / B ``nogate`` / C ``vanilla``) by a fixed rule, then given:
    * FSRS card state (``cards.data.$.s``) and a run of card reviews whose review
      time FALLS across exposures (``main.revlog.time``);
    * practice novel attempts (``is_holdout = 0``) spread across exposure buckets,
      with per-arm accuracy engineered to realize the PRE-REGISTERED crossover:
      Arm A slightly WORSE at exposures 1-4, BETTER at 5+ (a sign flip);
    * held-out novel attempts (``is_holdout = 1``) for the terminal contrast;
    * a persisted ``gap.retirements`` row when its arm's rule fires, engineered so
      Arm A retires FEWER concepts (the predicted throughput cost).

WHAT IT DOES
  Runs the canonical endpoint SQL (primary_crossover, terminal_novel_accuracy,
  throughput_cost, arm_c_sanity, latency_dissociation) over the combined data and
  prints a summary: the crossover contrast table (the sign flip), terminal A-B,
  throughput A vs B, latency r. Then writes ``sim/dashboard_data.json`` =
  ``gap.stats.dashboard_payload`` over the same data.

HONEST CAVEAT
  This is SIMULATED data built to exercise the analysis pipeline end-to-end. It is
  NOT an empirical result — the effects are engineered to the pre-registered shape.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap import stats  # noqa: E402
from gap.db import open_sidecar  # noqa: E402

SEED = 20260801
OUT_PATH = ROOT / "sim" / "dashboard_data.json"

# --- timeline constants (real-ish epoch ms so FSRS elapsed is sane) ---------- #
T0 = 1_700_000_000_000          # base epoch ms
HOUR = 3_600_000
DAY = 86_400_000
CONCEPT_GAP = 100_000_000       # disjoint id range per concept (no id collisions)
HOLDOUT_OFFSET = 50_000_000     # holdout ids sit after every review, within range

STUDENTS = 3
CONCEPTS = 40
FULL_REVIEWS = 8                # card exposures for a fully-studied concept
UNDER_REVIEWS = 3               # exposures for an under-studied (abstain) concept
PRACTICE_PER_BUCKET = 10        # practice novel attempts per bucket (full concepts)
UNDER_PRACTICE = 3              # practice attempts for under-studied concepts (1-4 only)
HOLDOUT_PER_CONCEPT = 10

# under-studied concept indices (fall BELOW the 8-attempt abstain line)
UNDER_IDX = {5, 15, 25, 35}

# Engineered correct-counts (out of PRACTICE_PER_BUCKET) per arm x bucket, chosen
# so the pooled contrast lands inside the pre-registered bands:
#   1-4: A - B in [-5, -12] pp   (Arm A worse)
#   5+ : A - B in [+6, +15] pp   (Arm A overtakes)
# Gate splits into H (retire: overall practice acc == 0.70, meets the >=0.7 gate)
# and L (not retired: overall 0.60).
C_GATE_H = {"14": 6, "5p": 8}   # 0.60 / 0.80  -> overall 0.70 -> retires (novel_gate)
C_GATE_L = {"14": 6, "5p": 6}   # 0.60 / 0.60  -> overall 0.60 -> not retired
C_NOGATE = {"14": 7, "5p": 6}   # 0.70 / 0.60
C_VANILLA = {"14": 6, "5p": 6}  # 0.60 / 0.60
C_UNDER = 1                     # 1 of 3 correct -> 0.333 (below-line concepts weaker)

# Held-out (terminal) correct-counts out of HOLDOUT_PER_CONCEPT.
H_GATE = 7      # 0.70
H_NOGATE = 6    # 0.60
H_VANILLA = 6   # 0.60

# Retirement targets (fewer for gate -> the predicted throughput cost).
GATE_RETIRE_FRACTION = 24 / 39     # of full gate concepts (24 of 39)
NOGATE_RETIRE_COUNT = 32           # of 36 full nogate concepts
VANILLA_RETIRE_COUNT = 30          # of 33 full vanilla concepts

CARD_S = 50.0    # FSRS stability
CARD_D = 5.0     # FSRS difficulty

ASSIGNED_MS = T0 - DAY  # arms assigned before first exposure


def arm_for(idx: int) -> str:
    return ("gate", "nogate", "vanilla")[idx % 3]


def corrects(rng: random.Random, n: int, k_correct: int) -> list[int]:
    """A length-n 0/1 list with exactly k_correct ones (means are exact; the RNG
    only shuffles WHICH positions, for realism — it never changes the count)."""
    vals = [1] * k_correct + [0] * (n - k_correct)
    rng.shuffle(vals)
    return vals


def build() -> tuple:
    """Seed the combined DB. Returns (gapdb, at_ms)."""
    rng = random.Random(SEED)
    tmp = tempfile.mkdtemp(prefix="gapsim_")
    gap_path = Path(tmp) / "gap.db"
    g = open_sidecar(None, gap_path, main_stub=True)

    # Enumerate all units in a fixed order; classify full/under and, for full
    # gate/nogate/vanilla, which are retired (deterministic by ordinal).
    units = []  # (student, idx, concept_id, arm, is_under)
    for s in range(STUDENTS):
        for idx in range(CONCEPTS):
            cid = (s + 1) * 1000 + idx
            units.append((s, idx, cid, arm_for(idx), idx in UNDER_IDX))

    full_gate = [u for u in units if u[3] == "gate" and not u[4]]
    full_nogate = [u for u in units if u[3] == "nogate" and not u[4]]
    full_vanilla = [u for u in units if u[3] == "vanilla" and not u[4]]
    n_gate_retire = round(GATE_RETIRE_FRACTION * len(full_gate))
    retired_gate = {u[2] for u in full_gate[:n_gate_retire]}
    retired_nogate = {u[2] for u in full_nogate[:NOGATE_RETIRE_COUNT]}
    retired_vanilla = {u[2] for u in full_vanilla[:VANILLA_RETIRE_COUNT]}

    con = g.db.con
    gci = 0
    global_last_review = 0
    for (s, idx, cid, arm, is_under) in units:
        base = T0 + gci * CONCEPT_GAP
        code = f"S{s}C{idx:02d}"
        weight = 1.0 + (idx % 5) * 0.3
        bdiff = 3.0 + (idx % 7) * 0.5

        con.execute(
            "INSERT INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
            " VALUES(?,?,?,?,?)",
            (cid, code, f"Concept {code}", weight, bdiff))

        # one note + one card, tagged with the concept code
        con.execute(
            "INSERT INTO notes(id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (cid, f"g{cid}", 1, 0, -1, f" concept::{code} ", "f", "f", 0, 0, "{}"))
        con.execute(
            "INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
            "reps,lapses,left,odue,odid,flags,data)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, cid, 1, 0, 0, -1, 2, 2, 0,
             30 if cid in retired_vanilla else 15,        # ivl (vanilla maturity proxy)
             2500, FULL_REVIEWS, 0, 0, 0, 0, 0,
             json.dumps({"s": CARD_S, "d": CARD_D})))

        # card reviews: time FALLS across exposures
        n_rev = UNDER_REVIEWS if is_under else FULL_REVIEWS
        for k in range(1, n_rev + 1):
            rid = base + k * HOUR
            con.execute(
                "INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, cid, -1, 3, 10, 5, 2500, 9000 - 800 * k, 1))
            global_last_review = max(global_last_review, rid)

        # ---- practice novel attempts (is_holdout = 0) ----
        if is_under:
            # below-line concept: UNDER_PRACTICE attempts in bucket 1-4, weak acc
            _seed_novel(con, rng, cid, base, holdout=False,
                        exposures=list(range(1, UNDER_PRACTICE + 1)),
                        n=UNDER_PRACTICE, k_correct=C_UNDER, j0=0)
        else:
            if arm == "gate":
                cc = C_GATE_H if cid in retired_gate else C_GATE_L
            elif arm == "nogate":
                cc = C_NOGATE
            else:
                cc = C_VANILLA
            _seed_novel(con, rng, cid, base, holdout=False,
                        exposures=[1, 2, 3, 4], n=PRACTICE_PER_BUCKET,
                        k_correct=cc["14"], j0=0)
            _seed_novel(con, rng, cid, base, holdout=False,
                        exposures=[5, 6, 7, 8], n=PRACTICE_PER_BUCKET,
                        k_correct=cc["5p"], j0=PRACTICE_PER_BUCKET)

        # ---- held-out novel attempts (is_holdout = 1) for the terminal endpoint ----
        h_correct = {"gate": H_GATE, "nogate": H_NOGATE, "vanilla": H_VANILLA}[arm]
        _seed_holdout(con, rng, cid, base, n=HOLDOUT_PER_CONCEPT, k_correct=h_correct)

        # ---- arm assignment (deterministic; assigned before first exposure) ----
        con.execute(
            "INSERT INTO gap.arms(concept_id,arm,assigned_ms) VALUES(?,?,?)",
            (cid, arm, ASSIGNED_MS))

        # ---- persisted retirement (row-presence == retired) ----
        retire = (cid in retired_gate or cid in retired_nogate or cid in retired_vanilla)
        if retire:
            trig = {"gate": "novel_gate", "nogate": "card_mastery",
                    "vanilla": "anki_default"}[arm]
            con.execute(
                "INSERT INTO gap.retirements(concept_id,retired_ms,trigger)"
                " VALUES(?,?,?)",
                (cid, base + (FULL_REVIEWS + 1) * HOUR, trig))
        gci += 1

    g.commit()
    # rebuild the derived note<->concept index from tags (real operational step)
    g.run_file("queries/01_open/rebuild_note_concepts.sql")
    g.commit()

    at_ms = global_last_review + HOUR
    return g, at_ms


def _seed_novel(con, rng, cid, base, holdout, exposures, n, k_correct, j0):
    """Insert n practice novel attempts, cycling through `exposures` so each sits
    at its intended exposure count as-of the attempt."""
    vals = corrects(rng, n, k_correct)
    for j in range(n):
        e = exposures[j % len(exposures)]
        rid = base + e * HOUR + 60_000 + (j0 + j) * 1000   # after review e, before e+1
        item_id = 10_000_000_000 + cid * 1000 + (j0 + j)
        con.execute(
            "INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
            " VALUES(?,?,?,?,?,?)",
            (item_id, f"ni{item_id}", f"src{cid}", 0, -1, 0))
        con.execute(
            "INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
            (item_id, cid))
        con.execute(
            "INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn) VALUES(?,?,?,?,?)",
            (rid, item_id, vals[j], 5000 + 300 * (e % 2), -1))  # latency flat/wobbly


def _seed_holdout(con, rng, cid, base, n, k_correct):
    """Insert n held-out (is_holdout = 1) novel attempts for the terminal contrast."""
    vals = corrects(rng, n, k_correct)
    for h in range(n):
        rid = base + HOLDOUT_OFFSET + h * 1000
        item_id = 20_000_000_000 + cid * 1000 + h
        con.execute(
            "INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
            " VALUES(?,?,?,?,?,?)",
            (item_id, f"ho{item_id}", f"hsrc{cid}", 1, -1, 0))
        con.execute(
            "INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
            (item_id, cid))
        con.execute(
            "INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn) VALUES(?,?,?,?,?)",
            (rid, item_id, vals[h], 5000, -1))


def _fmt(v, spec=".1f"):
    return "None" if v is None else format(v, spec)


def report(g) -> None:
    """Run the canonical endpoint SQL and print a clear summary."""
    print("=" * 70)
    print("SIMULATED STUDY — endpoint summary")
    print("=" * 70)

    # primary crossover — statement (b) is the descriptive contrast
    xover = g.query_all("queries/04_endpoints/primary_crossover.sql")[-1]
    print("\n[PRIMARY] crossover contrast (novel accuracy by arm x exposure bucket)")
    print("  bucket   n_gate n_nogate  acc_gate%  acc_nogate%   diff_pp (A-B)")
    for row in xover:
        bucket, n_gate, n_nogate, n_van, acc_g, acc_ng, acc_v, diff = row
        print(f"  {bucket:<6} {n_gate:>7} {n_nogate:>8}   {_fmt(acc_g):>8} "
              f"   {_fmt(acc_ng):>9}    {_fmt(diff):>7}")
    diffs = {r[0]: r[7] for r in xover}
    print(f"  -> sign flip: 1-4 = {_fmt(diffs.get('1-4'))} pp (A worse), "
          f"5+ = {_fmt(diffs.get('5+'))} pp (A overtakes)")

    # terminal
    term = g.query_all("queries/04_endpoints/terminal_novel_accuracy.sql")[-1][0]
    print("\n[SECONDARY] terminal held-out novel accuracy (A - B)")
    print(f"  gate={_fmt(term[1])}%  nogate={_fmt(term[2])}%  "
          f"A-B={_fmt(term[3])} pp  (n_gate={term[4]}, n_nogate={term[6]})")

    # arm C sanity
    csan = g.query_all("queries/04_endpoints/arm_c_sanity.sql")[-1][0]
    print("\n[SANITY] Arm A vs Arm C (gate - vanilla), held-out")
    print(f"  gate={_fmt(csan[1])}%  vanilla={_fmt(csan[2])}%  A-C={_fmt(csan[3])} pp")

    # throughput
    thr = g.query_all("queries/04_endpoints/throughput_cost.sql")[-1][0]
    print("\n[SECONDARY] throughput cost (retired concepts, A vs B)")
    print(f"  gate_retired={thr[0]}  nogate_retired={thr[4]}  "
          f"pct_diff_A_vs_B={_fmt(thr[12])}%  (predicted -25% to -35%)")

    # latency dissociation — statement (b) is the SQL Pearson r (sim has sqrt)
    lat = g.query_all("queries/04_endpoints/latency_dissociation.sql")[-1][0]
    print("\n[SECONDARY] latency dissociation")
    print(f"  n_points={lat[0]}  pearson_r={_fmt(lat[1], '.3f')}  verdict={lat[2]}")


def main() -> int:
    g, at_ms = build()
    report(g)

    payload = stats.dashboard_payload(g, at_ms=at_ms)
    OUT_PATH.write_text(json.dumps(payload, indent=2))

    xo = {c["bucket"]: c["diff_pp"] for c in payload["endpoints"]["crossover"]}
    print("\n" + "=" * 70)
    print(f"dashboard payload written: {OUT_PATH}")
    print(f"  crossover diff_pp: 1-4 = {_fmt(xo.get('1-4'))}, 5+ = {_fmt(xo.get('5+'))}")
    print(f"  abstain: scored={payload['abstain']['scored']} "
          f"abstained={payload['abstain']['abstained']} "
          f"threshold={payload['abstain']['threshold']}")
    print(f"  arms: {payload['arms']}")
    print("=" * 70)
    print("CAVEAT: this is SIMULATED data engineered to exercise the analysis "
          "pipeline end-to-end — NOT an empirical result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
