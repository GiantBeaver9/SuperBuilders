#!/usr/bin/env python3
"""Python integration test for the Rust ComputeReadinessGap RPC.

This calls the NEW Rust function across Anki's protobuf/FFI bridge — Python ->
backend -> Rust -> back — proving the engine change is reachable from the Python
layer. It MUST run against the FORK's pylib (which contains the RPC), not the
stock `anki` pip package (which does not). See rust-fork/README.md for building
and running it:

    # after `just wheels` in the fork:
    python -m venv /tmp/forkvenv
    /tmp/forkvenv/bin/pip install /home/user/anki-src/out/wheels/anki-*.whl
    /tmp/forkvenv/bin/python rust-fork/test_readiness_integration.py
"""
import os
import sys
import tempfile

from anki.collection import Collection
from anki import scheduler_pb2


def main() -> int:
    d = tempfile.mkdtemp()
    col = Collection(os.path.join(d, "collection.anki2"))
    model = col.models.by_name("Basic")
    did = col.decks.id("Default")

    # Concept A: 2 cards with FSRS state; B: 1 card with state; C: no cards.
    cids_a, cids_b = [], []
    for i in range(2):
        n = col.new_note(model); n["Front"] = f"A{i}"; n["Back"] = "a"
        col.add_note(n, did); cids_a.append(n.cards()[0].id)
    n = col.new_note(model); n["Front"] = "B0"; n["Back"] = "b"
    col.add_note(n, did); cids_b.append(n.cards()[0].id)

    # Give the cards FSRS memory state (stability s, difficulty d) via cards.data
    # — the keys Anki's CardData serialises — so the Rust Card.memory_state
    # populates. No review time -> elapsed 0 -> retrievability 1.0.
    for cid in cids_a + cids_b:
        col.db.execute("update cards set data=? where id=?", '{"s":40.0,"d":5.0}', cid)

    before = col.db.scalar("select coalesce(max(mod),0) from cards")

    concepts = [
        scheduler_pb2.ConceptGap(card_ids=cids_a, novel_accuracy=0.5,
                                 exam_weight=2.0, concept_code="A"),
        scheduler_pb2.ConceptGap(card_ids=cids_b, novel_accuracy=0.9,
                                 exam_weight=1.0, concept_code="B"),
        scheduler_pb2.ConceptGap(card_ids=[], novel_accuracy=0.3,
                                 exam_weight=1.0, concept_code="C"),
    ]

    # THE CALL: Python -> Rust RPC across the protobuf bridge.
    resp = col._backend.compute_readiness_gap(concepts=concepts)
    # Anki unwraps a single-field response: `resp` is the `ordered` repeated field.
    by = {s.concept_code: s for s in resp}

    # Concept with no cards -> mastery exactly 0.0.
    assert abs(by["C"].card_mastery - 0.0) < 1e-9, by["C"].card_mastery
    # Concepts with FSRS cards -> a real retrievability in (0, 1] computed by the
    # engine (the exact value depends on each card's elapsed time).
    for code in ("A", "B"):
        assert 0.0 < by[code].card_mastery <= 1.0 + 1e-6, (code, by[code].card_mastery)
    # points == (card_mastery - novel_accuracy) * exam_weight for EVERY concept,
    # using the mastery the Rust actually returned — proves the formula end-to-end.
    for s in resp:
        expected = (s.card_mastery - s.novel_accuracy) * s.exam_weight
        assert abs(s.points - expected) < 1e-5, (s.concept_code, s.points, expected)
    # The response is ordered by points, descending (largest gap first).
    pts = [s.points for s in resp]
    assert pts == sorted(pts, reverse=True), pts

    # read-only: the RPC must not mutate the collection
    after = col.db.scalar("select coalesce(max(mod),0) from cards")
    assert before == after, "ComputeReadinessGap must be read-only (cards.mod changed)"

    col.close()
    print("OK: Python called the Rust ComputeReadinessGap RPC — "
          "mastery/points/order correct, collection unchanged (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
