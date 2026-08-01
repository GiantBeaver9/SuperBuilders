#!/usr/bin/env python3
"""Simpler baselines the AI / structured method is evaluated against (PRD §4).

Two baselines, both intentionally naive so the comparison in
``ai/eval_generation.py`` is honest:

  * ``keyword_generate`` — naive keyword / first-sentence card extraction. It
    picks the document's most frequent content words and answers each with the
    document's FIRST sentence (the classic "the lead sentence is the
    definition" heuristic), citing that sentence's span. When the frequent
    keyword isn't what the first sentence defines, the card is not grounded —
    which is exactly what the grounding metric exposes.

  * ``TfidfIndex`` — a local TF-IDF cosine retrieval index, stdlib math only.
    It is the "vector / embedding search" baseline, and is a documented
    stand-in for a production embedding model: swap ``TfidfIndex`` for an
    embedding-backed index with the same ``fit`` / ``query`` interface and the
    eval is unchanged. ``KeywordOverlapIndex`` is the weaker retrieval baseline
    (raw term-frequency overlap, no IDF) that TF-IDF is compared against.

No external model downloads; no network.
"""
from __future__ import annotations

import math
from collections import Counter

from generate import content_tokens, split_sentences, tokens


# --------------------------------------------------------------------------- #
# Naive generation baseline
# --------------------------------------------------------------------------- #
def keyword_generate(source: dict, top_k: int = 4) -> list[dict]:
    """Naive keyword + first-sentence card extraction.

    front = "What is <frequent keyword>?", back = the document's first sentence,
    span = the first sentence. Returns cards in the same shape as
    ``generate.extract_cards`` so the eval can score them uniformly.
    """
    sents = split_sentences(source["text"])
    if not sents:
        return []
    first_sent, start, end = sents[0]
    freq = Counter(content_tokens(source["text"]))
    cards: list[dict] = []
    for word, _ in freq.most_common(top_k):
        cards.append({
            "source_id": source["source_id"],
            "source_span": [start, end],           # always cites sentence 1
            "front": f"What is {word}?",
            "back": first_sent,
            "concept_code": source["concept_code"],
            "method": "keyword",
        })
    return cards


# --------------------------------------------------------------------------- #
# Retrieval indexes
# --------------------------------------------------------------------------- #
class TfidfIndex:
    """TF-IDF cosine retrieval — the 'vector search' baseline.

    Stand-in for a production embedding model: the ``fit(docs)`` /
    ``query(q) -> ranked [(source_id, score)]`` interface is what an
    embedding-backed retriever would expose, so it drops in without touching
    the eval.
    """

    def __init__(self) -> None:
        self._idf: dict[str, float] = {}
        self._vecs: list[tuple[str, dict[str, float]]] = []

    def fit(self, docs: list[dict]) -> "TfidfIndex":
        n = len(docs)
        df: Counter = Counter()
        toked = []
        for d in docs:
            tf = Counter(tokens(d["text"]))
            toked.append((d["source_id"], tf))
            for term in tf:
                df[term] += 1
        # smoothed idf, floored at 0 so ubiquitous terms carry ~no weight
        self._idf = {t: max(0.0, math.log((1 + n) / (1 + c))) for t, c in df.items()}
        self._vecs = [(sid, self._weight(tf)) for sid, tf in toked]
        return self

    def _weight(self, tf: Counter) -> dict[str, float]:
        return {t: c * self._idf.get(t, 0.0) for t, c in tf.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def query(self, text: str) -> list[tuple[str, float]]:
        q = self._weight(Counter(tokens(text)))
        scored = [(sid, self._cosine(q, vec)) for sid, vec in self._vecs]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


class KeywordOverlapIndex:
    """Weaker retrieval baseline: raw term-frequency overlap, NO idf.

    Scores each doc by the summed document term frequency of the query's terms
    (stopwords included). Ubiquitous words like "is"/"the" inflate the score of
    whichever doc repeats them most, which is exactly where IDF-weighted TF-IDF
    pulls ahead.
    """

    def __init__(self) -> None:
        self._docs: list[tuple[str, Counter]] = []

    def fit(self, docs: list[dict]) -> "KeywordOverlapIndex":
        self._docs = [(d["source_id"], Counter(tokens(d["text"]))) for d in docs]
        return self

    def query(self, text: str) -> list[tuple[str, float]]:
        q = set(tokens(text))
        scored = [(sid, float(sum(tf[t] for t in q))) for sid, tf in self._docs]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
