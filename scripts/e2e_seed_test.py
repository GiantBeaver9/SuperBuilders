#!/usr/bin/env python3
"""End-to-end integration test on SEEDED data.

Builds main+gap, seeds a small engineered scenario, runs the operational
queries (which write to gap), then runs every analysis query against populated
tables to confirm they execute on real rows, and prints the primary crossover
contrast to show the committed sign flip is computed.
"""
import sqlite3, tempfile, re
from pathlib import Path

ROOT = Path("/home/user/SuperBuilders")


def statements(sql):
    # strip line comments, then split on ';' at statement end
    out = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        out.append(line)
    body = "\n".join(out)
    return [s.strip() for s in body.split(";") if s.strip()]


def run_file(con, path, show=False):
    for st in statements((ROOT / path).read_text()):
        cur = con.execute(st)
        if show and re.match(r"(?is)^\s*(with|select)", st):
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            print(f"    -> {cols}")
            for r in rows:
                print(f"       {r}")


with tempfile.TemporaryDirectory() as td:
    gap_path = Path(td) / "gap.db"
    g = sqlite3.connect(gap_path)
    g.executescript((ROOT / "schema" / "gap.sql").read_text())
    g.commit(); g.close()

    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "schema" / "main_stub.sql").read_text())
    con.execute("ATTACH DATABASE ? AS gap", (str(gap_path),))

    # --- seed gap.concepts: 4 concepts ---
    for cid, code in [(1, "C1"), (2, "C2"), (3, "C3"), (4, "C4")]:
        con.execute("INSERT INTO gap.concepts(id,code,name,weight) VALUES(?,?,?,?)",
                    (cid, code, f"Concept {code}", 1.0 + cid * 0.5))

    # --- seed main.notes + main.cards: one note/card per concept, tagged ---
    for cid, code in [(1, "C1"), (2, "C2"), (3, "C3"), (4, "C4")]:
        guid = f"g{cid}"
        con.execute("INSERT INTO notes(id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, guid, 1, 0, -1, f" concept::{code} ", "f", "f", 0, 0, "{}"))
        # cards.data carries FSRS state s (stability), d (difficulty)
        con.execute("INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
                    "reps,lapses,left,odue,odid,flags,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, cid, 1, 0, 0, -1, 2, 2, 0, 10, 2500, 6, 0, 0, 0, 0, 0,
                     '{"s": 40.0, "d": 5.0}'))
        # 6 exposures per card, ids 100..600 (+cid offset to keep ids distinct)
        for k in range(1, 7):
            rid = k * 100 + cid
            con.execute("INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (rid, cid, -1, 3, 10, 5, 2500, 8000 - k * 1000, 1))  # time falls w/ exposure

    # --- seed novel items: practice (holdout=0) for primary; one holdout for terminal ---
    # practice items: two per concept, one in bucket 1-4, one in 5+
    nid = 1000
    gate_concepts = {1, 2}   # engineered arms for the display
    for cid in (1, 2, 3, 4):
        for bucket, rlid, base in [("14", 250 + cid, 250), ("5p", 650 + cid, 650)]:
            item = nid; nid += 1
            con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                        " VALUES(?,?,?,?,?,?)", (item, f"ni{item}", f"src{cid}", 0, -1, 0))
            con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                        (item, cid))
            # engineered correctness to force the flip:
            #   gate:   wrong at 1-4, right at 5+   nogate: right at 1-4, wrong at 5+
            if cid in gate_concepts:
                correct = 0 if bucket == "14" else 1
            else:
                correct = 1 if bucket == "14" else 0
            # rlid interleaves with exposure ids (k*100+cid): 250+cid sees 2
            # exposures (bucket 1-4); 650+cid sees all 6 (bucket 5+).
            con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                        " VALUES(?,?,?,?,?)", (rlid, item, correct, 4000, -1))
        # one holdout item per concept (terminal endpoint)
        hitem = nid; nid += 1
        con.execute("INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
                    " VALUES(?,?,?,?,?,?)", (hitem, f"ni{hitem}", f"hsrc{cid}", 1, -1, 0))
        con.execute("INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)",
                    (hitem, cid))
        con.execute("INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
                    " VALUES(?,?,?,?,?)",
                    (900000 + hitem, hitem, 1 if cid in gate_concepts else 0, 4000, -1))
    con.commit()

    print("== operational: rebuild_note_concepts ==")
    run_file(con, "queries/01_open/rebuild_note_concepts.sql")
    n = con.execute("SELECT COUNT(*) FROM gap.note_concepts").fetchone()[0]
    print(f"   gap.note_concepts rows = {n} (expect 4)")

    print("== operational: assign_arms ==")
    run_file(con, "queries/02_assign/assign_arms.sql")
    arms = con.execute("SELECT arm, COUNT(*) FROM gap.arms GROUP BY arm").fetchall()
    print(f"   assigned arms = {arms}")

    # overwrite with engineered arms for the crossover display
    con.execute("DELETE FROM gap.arms")
    for cid in (1, 2, 3, 4):
        con.execute("INSERT INTO gap.arms(concept_id,arm,assigned_ms) VALUES(?,?,?)",
                    (cid, "gate" if cid in gate_concepts else "nogate", 1))
    con.commit()

    print("== primary_crossover (contrast statement) ==")
    run_file(con, "queries/04_endpoints/primary_crossover.sql", show=True)

    print("== every analysis query runs on populated data ==")
    for f in ["queries/03_queue/points_at_stake.sql",
              "queries/04_endpoints/terminal_novel_accuracy.sql",
              "queries/04_endpoints/throughput_cost.sql",
              "queries/04_endpoints/arm_c_sanity.sql",
              "queries/04_endpoints/latency_dissociation.sql",
              "queries/05_discipline/leakage_check.sql",
              "queries/05_discipline/abstain_rule.sql"]:
        try:
            run_file(con, f)
            print(f"   OK   {f}")
        except sqlite3.Error as e:
            print(f"   FAIL {f}: {e}")
    print("\nDONE")
