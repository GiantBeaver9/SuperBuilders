#!/usr/bin/env python3
"""Plain-python tests for the AI card-generation layer (asserts, then prints OK).

Covers PRD §4:
  1. Traceability: every generated card (deterministic AND model-authored AI set)
     resolves to a named source_id and carries a non-empty, in-bounds span.
  2. AI-disabled path runs with NO key and yields grounded cards; the guarded
     AI path raises a clear error when no key is set (no faked live call).
  3. Grounding: structured method beats the keyword baseline on the fixture.
  4. Retrieval: TF-IDF accuracy@1 beats the keyword-overlap baseline.
  5. Leakage: held-back (post-cutoff) sources are absent from the generation set.
  6. Both runnable scripts (generate.py, eval_generation.py) exit 0.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI = ROOT / "ai"
sys.path.insert(0, str(AI))

from baselines import KeywordOverlapIndex, TfidfIndex, keyword_generate  # noqa: E402
from eval_generation import accuracy_at_1, grounding_rate, is_grounded  # noqa: E402
from generate import (  # noqa: E402
    generate_all,
    generate_cards,
    load_catalog,
    load_generated_cards,
    source_by_id,
)


def _by_id():
    return {s["source_id"]: s for s in load_catalog(include_heldback=True)}


def _assert_traceable(cards, by_id, label):
    assert cards, f"{label}: expected some cards"
    for c in cards:
        sid = c["source_id"]
        src = by_id.get(sid)
        assert src is not None, f"{label}: unresolvable source_id {sid!r}"
        span = c["source_span"]
        assert isinstance(span, list) and len(span) == 2, f"{label}: bad span {span}"
        s, e = span
        assert 0 <= s < e <= len(src["text"]), f"{label}: span out of bounds {span}"
        assert src["text"][s:e].strip(), f"{label}: empty cited span"


def test_traceability():
    by_id = _by_id()
    structured = generate_all(mode="off")
    ai_cards = load_generated_cards()
    _assert_traceable(structured, by_id, "deterministic")
    _assert_traceable(ai_cards, by_id, "model-authored AI")
    # every AI card resolves through the public helper too
    for c in ai_cards:
        assert source_by_id(c["source_id"]) is not None
    print(f"OK  test_traceability  (deterministic={len(structured)}, ai={len(ai_cards)})")


def test_ai_disabled_runs_without_key_and_is_grounded():
    assert not os.environ.get("ANTHROPIC_API_KEY"), "test assumes no key in this env"
    assert not os.environ.get("OPENAI_API_KEY")
    by_id = _by_id()
    cards = generate_all(mode="off")            # the required AI-disabled build
    assert cards, "deterministic path produced no cards"
    grounded = [c for c in cards if is_grounded(c, by_id[c["source_id"]]["text"])]
    assert grounded, "deterministic path yielded no grounded cards"
    assert len(grounded) == len(cards), "some deterministic cards not grounded"
    print(f"OK  test_ai_disabled_runs_without_key_and_is_grounded  ({len(grounded)} grounded)")


def test_guarded_ai_path_errors_without_key():
    src = load_catalog(include_heldback=False)[0]
    raised = False
    try:
        generate_cards(src, mode="ai")
    except RuntimeError as exc:
        raised = True
        msg = str(exc).lower()
        assert "key" in msg and "no-ai" in msg, f"unclear error: {exc}"
    assert raised, "AI mode must raise a clear error when no key is set"
    print("OK  test_guarded_ai_path_errors_without_key")


def test_grounding_beats_keyword_baseline():
    by_id = _by_id()
    structured = generate_all(mode="off")
    keyword = [c for s in load_catalog(include_heldback=False) for c in keyword_generate(s)]
    g_struct = grounding_rate(structured, by_id)
    g_keyword = grounding_rate(keyword, by_id)
    assert g_struct > g_keyword, f"structured {g_struct} !> keyword {g_keyword}"
    print(f"OK  test_grounding_beats_keyword_baseline  (structured={g_struct:.3f} > keyword={g_keyword:.3f})")


def test_tfidf_retrieval_beats_keyword_overlap():
    everything = load_catalog(include_heldback=True)
    ai_cards = load_generated_cards()
    r_tfidf = accuracy_at_1(TfidfIndex().fit(everything), ai_cards)
    r_overlap = accuracy_at_1(KeywordOverlapIndex().fit(everything), ai_cards)
    assert r_tfidf > r_overlap, f"tfidf {r_tfidf} !> overlap {r_overlap}"
    print(f"OK  test_tfidf_retrieval_beats_keyword_overlap  (tfidf={r_tfidf:.3f} > overlap={r_overlap:.3f})")


def test_leakage_check_passes():
    active = load_catalog(include_heldback=False)
    everything = load_catalog(include_heldback=True)
    held = {s["source_id"] for s in everything if s["heldback"]}
    assert held, "fixture must include at least one held-back source"
    gen_ids = {c["source_id"] for c in generate_all(active, mode="off")}
    gen_ids |= {c["source_id"] for c in load_generated_cards()}
    assert not (gen_ids & held), f"leakage: held-back sources in generation set {gen_ids & held}"
    # and generating directly from a held-back source yields nothing
    for s in everything:
        if s["heldback"]:
            assert generate_cards(s, mode="off") == [], "held-back source produced cards"
    print(f"OK  test_leakage_check_passes  (held-back {sorted(held)} absent from generation)")


def test_runnable_scripts_exit_zero():
    for script in ("generate.py", "eval_generation.py"):
        proc = subprocess.run([sys.executable, str(AI / script)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{script} exited {proc.returncode}\n{proc.stderr}"
    print("OK  test_runnable_scripts_exit_zero  (generate.py, eval_generation.py)")


if __name__ == "__main__":
    test_traceability()
    test_ai_disabled_runs_without_key_and_is_grounded()
    test_guarded_ai_path_errors_without_key()
    test_grounding_beats_keyword_baseline()
    test_tfidf_retrieval_beats_keyword_overlap()
    test_leakage_check_passes()
    test_runnable_scripts_exit_zero()
    print("OK")
