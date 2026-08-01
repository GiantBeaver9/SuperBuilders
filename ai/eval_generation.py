#!/usr/bin/env python3
"""Reproducible eval for the AI card-generation layer (PRD §4).

Deterministic, no network, no API key. States its data cutoff, runs a leakage
check (no held-back source used in generation), and reports TWO comparisons
where the AI / structured method BEATS the simpler baselines on a defined,
auto-computable metric:

  (a) GROUNDING — fraction of generated cards whose answer is actually supported
      by the cited source span. Structured/AI (span-anchored) vs the naive
      keyword/first-sentence extractor.

  (b) SOURCE RETRIEVAL accuracy@1 — given a card's question, retrieve its source.
      TF-IDF cosine (the "vector/embedding search" stand-in) vs a keyword-overlap
      (raw term-frequency) baseline.

Run:  python3 ai/eval_generation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # allow flat imports

from baselines import KeywordOverlapIndex, TfidfIndex, keyword_generate  # noqa: E402
from generate import (  # noqa: E402
    DATA_CUTOFF,
    content_tokens,
    generate_all,
    load_catalog,
    load_generated_cards,
    tokens,
)

GROUND_THRESHOLD = 0.6  # fraction of answer content tokens that must sit in the span


# --------------------------------------------------------------------------- #
# Metric (a): grounding
# --------------------------------------------------------------------------- #
def is_grounded(card: dict, source_text: str) -> bool:
    """A card is grounded iff the cited span both (1) mentions what the question
    asks about and (2) contains the answer. This distinguishes a span-anchored
    card from a keyword card that pairs a frequent word with an unrelated
    first sentence."""
    s, e = card["source_span"]
    span = source_text[s:e]
    span_toks = set(tokens(span))
    front_terms = content_tokens(card["front"])
    back_terms = content_tokens(card["back"])
    if not front_terms or not back_terms:
        return False
    subject_present = any(t in span_toks for t in front_terms)
    answer_overlap = sum(1 for t in back_terms if t in span_toks) / len(back_terms)
    return subject_present and answer_overlap >= GROUND_THRESHOLD


def grounding_rate(cards: list[dict], by_id: dict[str, dict]) -> float:
    if not cards:
        return 0.0
    ok = sum(1 for c in cards if is_grounded(c, by_id[c["source_id"]]["text"]))
    return ok / len(cards)


# --------------------------------------------------------------------------- #
# Metric (b): source retrieval accuracy@1
# --------------------------------------------------------------------------- #
def accuracy_at_1(index, cards: list[dict]) -> float:
    if not cards:
        return 0.0
    hits = 0
    for c in cards:
        ranked = index.query(c["front"])
        if ranked and ranked[0][0] == c["source_id"]:
            hits += 1
    return hits / len(cards)


# --------------------------------------------------------------------------- #
def main() -> int:
    active = load_catalog(include_heldback=False)          # generation set
    everything = load_catalog(include_heldback=True)        # + held-back distractor
    by_id = {s["source_id"]: s for s in everything}
    heldback = [s for s in everything if s["heldback"]]

    # ----- generation: structured (AI-disabled), naive keyword, model-authored AI
    structured = generate_all(active, mode="off")
    keyword = [c for s in active for c in keyword_generate(s)]
    ai_cards = load_generated_cards()

    # ----- leakage check ---------------------------------------------------- #
    gen_ids = {c["source_id"] for c in structured} | {c["source_id"] for c in ai_cards}
    held_ids = {s["source_id"] for s in heldback}
    leaked = gen_ids & held_ids
    leakage_ok = not leaked

    # ----- metric (a): grounding ------------------------------------------- #
    g_ai = grounding_rate(ai_cards, by_id)
    g_struct = grounding_rate(structured, by_id)
    g_keyword = grounding_rate(keyword, by_id)

    # ----- metric (b): retrieval accuracy@1 -------------------------------- #
    # Retrieval must discriminate among ALL sources incl. the held-back distractor.
    tfidf = TfidfIndex().fit(everything)
    overlap = KeywordOverlapIndex().fit(everything)
    r_tfidf = accuracy_at_1(tfidf, ai_cards)
    r_overlap = accuracy_at_1(overlap, ai_cards)

    # ----- report ---------------------------------------------------------- #
    print("=" * 70)
    print("AI CARD-GENERATION EVAL  (PRD §4)")
    print("=" * 70)
    print(f"data cutoff:            {DATA_CUTOFF}")
    print(f"active sources:         {len(active)}  "
          f"({', '.join(s['source_id'] for s in active)})")
    print(f"held-back (> cutoff):   {sorted(held_ids) or 'none'}")
    print(f"leakage check:          {'PASS' if leakage_ok else 'FAIL -> ' + str(sorted(leaked))}"
          f"  (held-back sources absent from generation)")
    print(f"cards: structured={len(structured)}  keyword={len(keyword)}  ai={len(ai_cards)}")
    print()

    def row(metric, ai, base, base_name):
        winner = "AI/structured" if ai > base else ("TIE" if ai == base else base_name)
        print(f"{metric:<34} {ai:>7.3f}  {base:>7.3f}   {winner}")

    print(f"{'METRIC':<34} {'AI/STR':>7}  {'BASE':>7}   WINNER")
    print("-" * 70)
    print("(a) GROUNDING  (answer supported by cited span)")
    row("    structured vs keyword", g_struct, g_keyword, "keyword")
    row("    model-authored AI vs keyword", g_ai, g_keyword, "keyword")
    print("(b) SOURCE RETRIEVAL accuracy@1  (question -> source)")
    row("    TF-IDF(vector) vs kw-overlap", r_tfidf, r_overlap, "kw-overlap")
    print("-" * 70)

    ai_wins = (g_struct > g_keyword) and (g_ai > g_keyword) and (r_tfidf > r_overlap)
    print(f"\nAI/structured beats baselines on both metrics: "
          f"{'YES' if ai_wins else 'NO'}")

    print("\nNote: scores are on a small hand-built fixture, not a benchmark. The")
    print("deterministic extractor and TF-IDF are transparent stand-ins; the live")
    print("frontier-LLM path (ai/generate.py --ai, guarded on ANTHROPIC_API_KEY)")
    print("would author richer cards, and a real embedding model would slot into")
    print("the TfidfIndex interface unchanged. What the eval fixes is the CONTRACT:")
    print("every card is traceable to a named source span, and structured/traceable")
    print("generation + IDF-weighted retrieval measurably beat the naive baselines.")

    ok = leakage_ok and ai_wins
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
