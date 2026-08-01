#!/usr/bin/env python3
"""Engine test for gap.queue and gap.retirement.

Plain script (asserts + a final "OK"), no pytest. Three parts:

(a) LIVE PATH on a REAL headless Anki collection (`anki.collection.Collection`):
    create tagged notes/cards, seed gap.concepts, attach the sidecar, rebuild the
    membership index (01_open), assign arms (02_assign), then run
    queue.points_at_stake and retirement.evaluate on real Anki — proving they run
    with NO math-function errors (Anki's SQLite has no pow/exp/sqrt).

(b) RETIREMENT triggers fire correctly per arm on engineered data, and a second
    evaluate() call does NOT re-retire (idempotent).

(c) PARITY on an offline open_sidecar(main_stub=True) DB (which HAS sqlite math):
    the Python points_at_stake card_mastery equals the canonical SQL value from
    queries/03_queue/points_at_stake.sql (the pow path) within 1e-9.

Run: cd /home/user/SuperBuilders && python3 tests/test_queue_retirement.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gap import mastery, queue, retirement                      # noqa: E402
from gap.db import GapDB, ensure_sidecar_schema, attach_gap, open_sidecar  # noqa: E402

DAY = mastery.DAY_MS

# Full main_stub / real-Anki cards column list, so INSERTs are unambiguous.
_CARD_COLS = ("id", "nid", "did", "ord", "mod", "usn", "type", "queue", "due",
              "ivl", "factor", "reps", "lapses", "left", "odue", "odid", "flags",
              "data")


# --------------------------------------------------------------------------- #
# (a) + (b): the live path on a REAL headless Anki collection.
# --------------------------------------------------------------------------- #
def test_live_anki() -> None:
    from anki.collection import Collection

    td = tempfile.mkdtemp()
    gap_path = os.path.join(td, "gap.db")
    ensure_sidecar_schema(gap_path)
    col = Collection(os.path.join(td, "collection.anki2"))
    try:
        at = mastery.now_ms()

        # concept_id, code, arm, weight, stability, elapsed_days, ivl, novel_acc
        #   novel_acc = None -> no practice novel attempts for the concept.
        # Engineered so each arm has one concept that SHOULD retire and one that
        # SHOULD NOT.
        specs = [
            (1, "G_PASS",  "gate",    3.0, 50.0,      2.0, 15, 1.0),   # gate: acc>=0.7 -> retire
            (2, "G_FAIL",  "gate",    2.0, 50.0,      2.0, 15, 0.0),   # gate: acc<0.7  -> no
            (3, "N_PASS",  "nogate",  1.0, 100_000.0, 1.0, 25, None),  # nogate: R>=0.9 -> retire
            (4, "N_FAIL",  "nogate",  1.0, 1.0,      30.0, 25, None),  # nogate: R<0.9  -> no
            (5, "V_PASS",  "vanilla", 1.5, 50.0,      2.0, 30, None),  # vanilla: ivl>=21 -> retire
            (6, "V_FAIL",  "vanilla", 1.5, 50.0,      2.0, 10, None),  # vanilla: ivl<21  -> no
        ]

        attach_gap(col.db, gap_path)
        g = GapDB(col.db)

        model = col.models.by_name("Basic")
        did = col.decks.id("Default")
        code_to_cid: dict[str, int] = {}   # concept code -> Anki card id

        for i, (cid, code, _arm, weight, s, elapsed_days, ivl, _acc) in enumerate(specs):
            g.execute(
                "INSERT INTO gap.concepts(id, code, name, weight) VALUES (?, ?, ?, ?)",
                cid, code, f"Concept {code}", weight,
            )
            note = col.new_note(model)
            note.fields[0] = f"front {code}"
            note.fields[1] = f"back {code}"
            note.tags = [f"concept::{code}"]
            col.add_note(note, did)
            card_id = note.card_ids()[0]
            code_to_cid[code] = card_id

            # Make the card DUE (review queue, due<=now) and give it FSRS state.
            data = '{"s": %r, "d": 5.0}' % s
            col.db.execute(
                "UPDATE cards SET type=2, queue=2, due=0, ivl=?, data=? WHERE id=?",
                ivl, data, card_id,
            )
            # One review so the card has a last_review_ms (MAX(revlog.id)); the id
            # sets elapsed. +i keeps the revlog id unique across equal-elapsed cards.
            last_review = at - int(elapsed_days * DAY) + i
            col.db.execute(
                "INSERT INTO revlog(id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                last_review, card_id, -1, 3, ivl, 10, 2500, 5000, 1,
            )

        # Practice novel attempts (is_holdout=0) for the gate concepts.
        item = 10_000
        for cid, code, _arm, _w, _s, _e, _ivl, acc in specs:
            if acc is None:
                continue
            col.db.execute(
                "INSERT INTO gap.novel_items(id, guid, source_id, is_holdout, usn, mod)"
                " VALUES (?, ?, ?, ?, ?, ?)", item, f"ni{item}", f"src{cid}", 0, -1, 0)
            col.db.execute(
                "INSERT INTO gap.novel_item_concepts(item_id, concept_id) VALUES (?, ?)",
                item, cid)
            col.db.execute(
                "INSERT INTO gap.novel_revlog(id, item_id, correct, time, usn)"
                " VALUES (?, ?, ?, ?, ?)", item, item, int(acc), 4000, -1)
            item += 1

        # 01_open: rebuild the note->concept membership index from the tags.
        g.run_file("queries/01_open/rebuild_note_concepts.sql")
        n_mem = g.scalar("SELECT COUNT(*) FROM gap.note_concepts")
        assert n_mem == len(specs), f"note_concepts={n_mem}, expected {len(specs)}"

        # 02_assign: prove the arm-assignment query runs LIVE on Anki (ntile,
        # recursive CTE, no math functions) without error.
        g.run_file("queries/02_assign/assign_arms.sql")
        n_arms = g.scalar("SELECT COUNT(*) FROM gap.arms")
        assert n_arms == len(specs), f"arms={n_arms}, expected {len(specs)}"

        # Overwrite with the ENGINEERED arms so retirement rules are testable
        # per arm (the real split is randomized/stratified). assigned_ms=1 so the
        # eval instant `at` postdates assignment.
        col.db.execute("DELETE FROM gap.arms")
        for cid, _code, arm, *_ in specs:
            col.db.execute(
                "INSERT INTO gap.arms(concept_id, arm, assigned_ms) VALUES (?, ?, ?)",
                cid, arm, 1)

        # ---- (a) queue.points_at_stake runs on REAL Anki, no math error ----
        ranked = queue.points_at_stake(g, at_ms=at)
        assert len(ranked) == len(specs), f"due cards={len(ranked)}"
        keys = {"card_id", "concept_id", "code", "card_mastery",
                "novel_accuracy", "weight", "points"}
        for row in ranked:
            assert set(row) == keys, f"dict keys {set(row)} != {keys}"
        # ordered by points DESC
        pts = [r["points"] for r in ranked]
        assert pts == sorted(pts, reverse=True), f"not sorted desc: {pts}"
        # a concept with no practice novel attempts surfaces with novel=0.0
        n_pass = next(r for r in ranked if r["code"] == "N_PASS")
        assert n_pass["novel_accuracy"] == 0.0, n_pass
        # ranked_card_ids agrees with the full ranking projection
        assert queue.ranked_card_ids(g, at_ms=at) == [r["card_id"] for r in ranked]

        # ---- (b) retirement.evaluate fires per arm, then is idempotent ----
        fired = retirement.evaluate(g, at_ms=at)
        got = {row["concept_id"]: (row["arm"], row["trigger"]) for row in fired}
        expect = {
            1: ("gate", "novel_gate"),      # G_PASS
            3: ("nogate", "card_mastery"),  # N_PASS
            5: ("vanilla", "anki_default"), # V_PASS
        }
        assert got == expect, f"retired {got}, expected {expect}"

        # the FAIL concepts must NOT be retired
        for cid in (2, 4, 6):
            assert not retirement.is_retired(g, cid), f"concept {cid} wrongly retired"
        for cid in (1, 3, 5):
            assert retirement.is_retired(g, cid), f"concept {cid} not retired"

        assert retirement.retired_concepts(g) == {
            1: "novel_gate", 3: "card_mastery", 5: "anki_default"}

        # Idempotent: a second evaluate retires nothing new and leaves the set.
        again = retirement.evaluate(g, at_ms=at + DAY)
        assert again == [], f"second evaluate re-retired: {again}"
        assert retirement.retired_concepts(g) == {
            1: "novel_gate", 3: "card_mastery", 5: "anki_default"}

        print("  (a) live Anki queue + (b) retirement per-arm/idempotent: OK")
    finally:
        col.close()


# --------------------------------------------------------------------------- #
# (c): parity between Python card_mastery and the canonical SQL pow() path.
# --------------------------------------------------------------------------- #
def test_parity_with_sql() -> None:
    td = tempfile.mkdtemp()
    gap_path = os.path.join(td, "gap.db")
    # open_sidecar seeds an in-memory main via main_stub (which HAS sqlite math).
    g = open_sidecar(None, gap_path, main_stub=True, sql_root=ROOT)
    at = mastery.now_ms()

    # One concept, one due card with a stable FSRS state. Large stability so the
    # tiny difference between Python's at_ms and the SQL's own unixepoch("now")
    # damps far below the 1e-9 tolerance.
    g.execute("INSERT INTO gap.concepts(id, code, name, weight) VALUES (?, ?, ?, ?)",
              1, "P1", "Concept P1", 2.0)
    g.execute(
        "INSERT INTO notes(id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        1, "gp1", 1, 0, -1, " concept::P1 ", "f", "f", 0, 0, "{}")
    g.execute(
        "INSERT INTO cards(" + ", ".join(_CARD_COLS) + ") VALUES ("
        + ", ".join(["?"] * len(_CARD_COLS)) + ")",
        1, 1, 1, 0, 0, -1, 2, 2, 0, 20, 2500, 6, 0, 0, 0, 0, 0,
        '{"s": 100000.0, "d": 5.0}')
    last_review = at - 5 * DAY
    g.execute(
        "INSERT INTO revlog(id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        last_review, 1, -1, 3, 20, 10, 2500, 5000, 1)
    g.commit()

    g.run_file("queries/01_open/rebuild_note_concepts.sql")
    g.commit()

    # Canonical SQL ranking (pow path). Columns:
    #   card_id, concept_id, code, card_mastery, novel_accuracy, weight, points
    sql_rows = g.query("queries/03_queue/points_at_stake.sql")
    sql_mastery = {r[1]: r[3] for r in sql_rows}

    # Python ranking (math-free SQL + gap.mastery in Python).
    py_rows = queue.points_at_stake(g, at_ms=at)
    py_mastery = {r["concept_id"]: r["card_mastery"] for r in py_rows}

    assert 1 in sql_mastery and 1 in py_mastery, (sql_mastery, py_mastery)
    diff = abs(sql_mastery[1] - py_mastery[1])
    assert diff < 1e-9, f"card_mastery parity off by {diff}: sql={sql_mastery[1]} py={py_mastery[1]}"
    print(f"  (c) SQL vs Python card_mastery parity: |diff|={diff:.2e} < 1e-9: OK")


if __name__ == "__main__":
    test_live_anki()
    test_parity_with_sql()
    print("OK")
