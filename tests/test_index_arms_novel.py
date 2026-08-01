"""Engine test: gap.index, gap.arms, gap.novel against a REAL headless Anki.

Plain python script (asserts + final "OK", no pytest). Proves the live-path
modules run on a real `anki.collection.Collection` with the sidecar attached
onto Anki's own `col.db`, and exercises the novel-item CRUD/logging invariants.

Run:  cd /home/user/SuperBuilders && python3 tests/test_index_arms_novel.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anki.collection import Collection

from gap import arms, index, novel
from gap.db import GapDB, attach_gap, ensure_sidecar_schema


def _add_note(col, deck_id, tags: list[str]) -> str:
    """Add a Basic note with the given tags; return its guid."""
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = "Q " + " ".join(tags)
    note["Back"] = "A"
    note.tags = list(tags)
    col.add_note(note, deck_id)
    return note.guid


def main() -> None:
    workdir = tempfile.mkdtemp(prefix="gap_test_")
    col_path = os.path.join(workdir, "collection.anki2")
    gap_path = os.path.join(workdir, "gap.db")

    col = Collection(col_path)
    try:
        deck_id = col.decks.id("Default")

        # Nine known concepts (tagged on notes) plus one UNKNOWN code (9Z.9)
        # that is never seeded — used later for ensure_concepts_from_tags.
        # Nine concepts (with distinct weights) split into 3 strata of 3, so
        # round-robin assignment lands all three arms (gate/nogate/vanilla).
        known = [f"1A.{i}" for i in range(1, 10)]
        for code in known:
            _add_note(col, deck_id, [f"concept::{code}"])            # 9 single pairs
        _add_note(col, deck_id, ["concept::1A.1", "concept::1A.2"])  # +2 pairs
        _add_note(col, deck_id, ["concept::9Z.9"])                    # unknown only
        _add_note(col, deck_id, ["concept::1A.3", "concept::9Z.9"])   # +1 known pair

        # Attach the sidecar onto Anki's own col.db (the real add-on path).
        ensure_sidecar_schema(gap_path)
        attach_gap(col.db, gap_path)
        g = GapDB(col.db)

        # Seed the nine KNOWN concepts with distinct weights so the tercile
        # stratification has something to bin on.
        for cid, code in enumerate(known, start=1):
            g.execute(
                "INSERT INTO gap.concepts (id, code, name, weight) VALUES (?, ?, ?, ?)",
                cid, code, code, float(cid),
            )

        # ---- (a) index.rebuild + arms.assign on REAL Anki ------------------ #
        n = index.rebuild(g)
        # Known (guid, concept) pairs: 9 single-tag + 2 (1A.1/1A.2 note)
        # + 1 (1A.3 in the mixed note) = 12. The 9Z.9 tags are ignored (no
        # matching gap.concepts row yet).
        assert n == 12, f"rebuild count expected 12, got {n}"
        # Idempotent rebuild.
        assert index.rebuild(g) == 12, "rebuild not idempotent"

        splits = arms.assign(g)
        assert set(splits) == {"gate", "nogate", "vanilla"}, \
            f"arms not split into three: {splits}"
        assert sum(splits.values()) == 9, f"arm total expected 9, got {splits}"
        # Idempotent assignment.
        assert arms.assign(g) == splits, "assign not idempotent"

        # arm_of for a known / unknown concept.
        assert arms.arm_of(g, 1) in {"gate", "nogate", "vanilla"}
        assert arms.arm_of(g, 999) is None

        # ---- (c) ensure_concepts_from_tags: inserts missing, idempotent ---- #
        inserted = arms.ensure_concepts_from_tags(g)
        assert inserted == 1, f"expected 1 concept inserted (9Z.9), got {inserted}"
        assert arms.ensure_concepts_from_tags(g) == 0, "ensure not idempotent"
        # The new code is now a real concept row with weight 1.0.
        new_id = g.scalar("SELECT id FROM gap.concepts WHERE code = '9Z.9'")
        assert new_id is not None
        assert g.scalar("SELECT weight FROM gap.concepts WHERE code = '9Z.9'") == 1.0
        # Now 9Z.9 participates: rebuild picks up the two 9Z.9 notes = 2 more.
        assert index.rebuild(g) == 14, "rebuild did not pick up newly-ensured concept"

        # ---- (b) novel: add_item / record_attempt / next_item / counts ----- #
        # Concept 1 is a real gap.concepts row (FK-safe).
        p1 = novel.add_item(g, guid="np1", source_id="src", is_holdout=False,
                            concept_ids=[1], mod_ms=1001)
        p2 = novel.add_item(g, guid="np2", source_id="src", is_holdout=False,
                            concept_ids=[1], mod_ms=1002)
        h1 = novel.add_item(g, guid="nh1", source_id="src", is_holdout=True,
                            concept_ids=[1], mod_ms=2001)
        assert (p1, p2, h1) == (1001, 1002, 2001)

        # is_holdout stored exactly as set at insert.
        assert g.scalar("SELECT is_holdout FROM gap.novel_items WHERE id=1001") == 0
        assert g.scalar("SELECT is_holdout FROM gap.novel_items WHERE id=2001") == 1

        # No attempts yet: practice tie broken by lowest id -> 1001.
        assert novel.next_item(g, 1, holdout=False) == 1001
        # Holdout pool is separate: only h1 qualifies.
        assert novel.next_item(g, 1, holdout=True) == 2001

        # Attempt on 1001 -> 1002 now has the fewest practice attempts.
        r1 = novel.record_attempt(g, 1001, correct=True, time_ms=1200, at_ms=5001)
        assert r1 == 5001
        assert novel.next_item(g, 1, holdout=False) == 1002

        # Append-only: a SECOND attempt on 1001 adds a row, never overwrites.
        novel.record_attempt(g, 1001, correct=False, time_ms=900, at_ms=5002)
        rows_1001 = g.scalar("SELECT COUNT(*) FROM gap.novel_revlog WHERE item_id=1001")
        assert rows_1001 == 2, f"append-only broken: {rows_1001} rows for 1001"

        # 1002 still least attended (0 vs 2) until it gets an attempt.
        assert novel.next_item(g, 1, holdout=False) == 1002
        novel.record_attempt(g, 1002, correct=True, time_ms=1000, at_ms=5003)
        # Now 1001 has 2, 1002 has 1 -> fewest is 1002.
        assert novel.next_item(g, 1, holdout=False) == 1002

        # Holdout attempts are counted separately from practice.
        novel.record_attempt(g, 2001, correct=True, time_ms=1100, at_ms=6001)

        practice_counts = novel.attempt_counts(g, holdout=False)
        holdout_counts = novel.attempt_counts(g, holdout=True)
        # Practice: 2 on 1001 + 1 on 1002 = 3, all concept 1.
        assert practice_counts == {1: 3}, f"practice counts wrong: {practice_counts}"
        # Holdout: 1 on 2001, concept 1 — strictly separated from practice.
        assert holdout_counts == {1: 1}, f"holdout counts wrong: {holdout_counts}"

        # next_item returns None when a concept has no item in that pool.
        assert novel.next_item(g, 2, holdout=False) is None

        print("OK")
    finally:
        col.close()


if __name__ == "__main__":
    main()
