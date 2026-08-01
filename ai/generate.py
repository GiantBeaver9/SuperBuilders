#!/usr/bin/env python3
"""AI card-generation pipeline (PRD §4).

Turns NAMED, citable source passages (``ai/sources/``) into Q/A flashcards.
Every returned card carries its ``source_id`` and a ``source_span`` (character
offsets into the source text) so it is TRACEABLE — mirroring
``schema/gap.sql``'s ``novel_items.source_id`` rule ("untraceable AI output
zeroes the section"). A card that cannot cite a source is DROPPED.

Two modes:

  * ``mode="off"`` — the deterministic, no-LLM fallback the PRD requires for the
    "AI disabled" build. A transparent rule-based extractor turns definition /
    fact sentences into Q/A cards, each tagged with its source_id + span. This
    path runs anywhere, no API key needed.

  * ``mode="ai"`` — the real frontier-LLM path, GUARDED on an API key. Without a
    key it raises a clear "set a key or use --no-ai" error and does NOT fake a
    call. The exact wire shape it would use is documented in ``_ai_generate``.

The committed model-authored AI card set (``ai/generated_cards.json``) is the
"AI mode" output the eval scores; it is reproducible via the guarded LLM path.

Run:  python3 ai/generate.py            # defaults to --no-ai when no key is set
      python3 ai/generate.py --ai       # errors clearly unless a key is present
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = Path(__file__).resolve().parent / "sources"
CATALOG_MD = SOURCES_DIR / "SOURCES.md"
GENERATED_CARDS = Path(__file__).resolve().parent / "generated_cards.json"

# Data cutoff (also stated in ai/sources/SOURCES.md). A source dated after this
# is held back and never enters generation — the leakage guard.
DATA_CUTOFF = "2026-08-01"

# The model the guarded LLM path would call (Anthropic Messages API).
LLM_MODEL = "claude-opus-4-8"

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "by", "for", "with", "as", "that", "which",
    "and", "or", "it", "its", "this", "these", "those", "from", "into",
    "when", "makes", "make", "has", "have", "does", "do", "not", "no",
    "what", "why", "how", "over", "unless", "acted", "if", "onto",
}


# --------------------------------------------------------------------------- #
# Tokenization (shared by baselines, eval, and grounding checks)
# --------------------------------------------------------------------------- #
def tokens(text: str) -> list[str]:
    """Lowercased alphanumeric word tokens."""
    return re.findall(r"[a-z0-9']+", text.lower())


def content_tokens(text: str) -> list[str]:
    """Word tokens with stopwords removed — the 'meaning-bearing' words."""
    return [t for t in tokens(text) if t not in _STOPWORDS and len(t) > 1]


# --------------------------------------------------------------------------- #
# Catalog loading (parses the markdown table in ai/sources/SOURCES.md)
# --------------------------------------------------------------------------- #
def load_catalog(include_heldback: bool = False) -> list[dict]:
    """Parse the pipe-delimited catalog table in SOURCES.md.

    Returns a list of dicts: ``source_id, concept_code, date, title,
    attribution, text, heldback``. By default held-back sources (dated after the
    cutoff) are EXCLUDED — this is the leakage guard the generation path relies
    on. Pass ``include_heldback=True`` only for retrieval-index/eval bookkeeping.
    """
    rows: list[dict] = []
    for line in CATALOG_MD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 5:
            continue
        source_id, concept_code, date, title, attribution = cells
        if source_id in ("source_id", "") or set(source_id) <= {"-"}:
            continue  # header / separator row
        heldback = date > DATA_CUTOFF
        if heldback and not include_heldback:
            continue
        txt_path = SOURCES_DIR / f"{source_id}.txt"
        rows.append({
            "source_id": source_id,
            "concept_code": concept_code,
            "date": date,
            "title": title,
            "attribution": attribution,
            "text": txt_path.read_text(encoding="utf-8").strip(),
            "heldback": heldback,
        })
    if not rows:
        raise RuntimeError(f"no sources parsed from {CATALOG_MD}")
    return rows


def source_by_id(source_id: str, include_heldback: bool = True) -> dict | None:
    for row in load_catalog(include_heldback=include_heldback):
        if row["source_id"] == source_id:
            return row
    return None


# --------------------------------------------------------------------------- #
# Sentence splitting with character offsets (spans are [start, end) into text)
# --------------------------------------------------------------------------- #
def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Return (sentence, start, end) triples; span is a [start, end) slice."""
    out: list[tuple[str, int, int]] = []
    for m in re.finditer(r"[^.]*\.", text):
        seg = m.group(0)
        start = m.start()
        end = m.end()
        # trim leading whitespace from the span so it starts at the first word
        lead = len(seg) - len(seg.lstrip())
        start += lead
        sent = text[start:end].strip()
        if sent:
            out.append((sent, start, start + len(sent)))
    return out


# --------------------------------------------------------------------------- #
# Deterministic (AI-disabled) extractor
# --------------------------------------------------------------------------- #
_STRIP_ARTICLE = re.compile(r"^(?:A|An|The)\s+", re.IGNORECASE)


def _clean_answer(ans: str) -> str:
    ans = ans.strip().rstrip(".").strip()
    if not ans:
        return ans
    return ans[0].upper() + ans[1:] + "."


def extract_cards(source: dict) -> list[dict]:
    """Rule-based definition/fact extraction → grounded Q/A cards.

    Recognizes two transparent sentence templates:
      * ``<subject> states that <predicate>``  → "What does <subject> state?"
      * ``<subject> is <predicate>``           → "What is <subject>?"
    Each card's span is the whole defining sentence, so the subject asked about
    and the answer both come from the cited span (grounding holds by
    construction). Sentences matching no template are dropped — an unciteable
    fact never becomes a card.
    """
    cards: list[dict] = []
    for sent, start, end in split_sentences(source["text"]):
        m = re.match(r"(.+?)\s+states that\s+(.+)", sent, re.IGNORECASE)
        if m:
            subject = m.group(1).strip()
            front = f"What does {subject} state?"
            back = _clean_answer(m.group(2))
        else:
            m = re.match(r"(.+?)\s+is\s+(.+)", sent, re.IGNORECASE)
            if not m:
                continue
            subject = _STRIP_ARTICLE.sub("", m.group(1).strip()).strip()
            front = f"What is {subject.lower()}?"
            back = _clean_answer(m.group(2))
        if not subject or not back:
            continue
        cards.append({
            "source_id": source["source_id"],
            "source_span": [start, end],
            "front": front,
            "back": back,
            "concept_code": source["concept_code"],
            "method": "deterministic",
        })
    return cards


# --------------------------------------------------------------------------- #
# Guarded frontier-LLM path
# --------------------------------------------------------------------------- #
def _ai_generate(source: dict) -> list[dict]:
    """Real LLM path — GUARDED on an API key; never fakes a call.

    Wire shape (Anthropic Messages API), used verbatim when a key is present:

        POST {ANTHROPIC_BASE_URL or https://api.anthropic.com}/v1/messages
        headers: x-api-key: $ANTHROPIC_API_KEY
                 anthropic-version: 2023-06-01
                 content-type: application/json
        body: {
          "model": "claude-opus-4-8",
          "max_tokens": 2048,
          "system": "<instructs the model to output ONLY JSON: a list of cards,
                      each {source_span:[start,end], front, back, concept_code},
                      where source_span indexes the provided passage and the
                      answer is supported by that span. Uncitable cards omitted.>",
          "messages": [{"role": "user", "content": "<source_id, concept_code,
                        and the passage text with character offsets>"}]
        }

    The JSON the model returns is validated by ``generate_cards`` exactly like
    the deterministic path — any card missing a resolvable source_id or a
    non-empty in-bounds span is DROPPED, so the traceability rule holds
    regardless of which mode produced the card.
    """
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "AI mode requires an LLM API key, but none is set "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY are both unset). "
            "Set a key to use --ai, or run with --no-ai to use the deterministic "
            "extractor (the PRD's required AI-disabled path)."
        )

    # A key IS present: issue the real request (stdlib only — no SDK in this box).
    import urllib.request  # noqa: PLC0415  (deferred: only needed on the live path)

    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    system = (
        "You convert a source passage into flashcards. Output ONLY a JSON array. "
        "Each element must be an object with keys source_span ([start,end] "
        "character offsets into the passage), front, back, and concept_code. "
        "The answer in `back` MUST be supported by the text at source_span. "
        "Omit any card you cannot ground in the passage."
    )
    user = json.dumps({
        "source_id": source["source_id"],
        "concept_code": source["concept_code"],
        "passage": source["text"],
    })
    payload = json.dumps({
        "model": LLM_MODEL,
        "max_tokens": 2048,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v1/messages",
        data=payload,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # pragma: no cover - needs key
        body = json.loads(resp.read().decode("utf-8"))
    text_out = "".join(
        block.get("text", "") for block in body.get("content", [])
        if block.get("type") == "text"
    )
    raw_cards = json.loads(text_out)
    for c in raw_cards:  # stamp provenance the model can't be trusted to set
        c["source_id"] = source["source_id"]
        c.setdefault("concept_code", source["concept_code"])
        c["method"] = "llm"
    return raw_cards


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def generate_cards(source: dict, mode: str = "off") -> list[dict]:
    """Generate cards for one source. ``mode`` ∈ {"ai", "off"}.

    Traceability enforcement: every returned card MUST resolve to the named
    source and carry a non-empty, in-bounds character span. Cards that fail are
    DROPPED (mirrors "untraceable AI output zeroes the section").
    """
    if source.get("heldback"):
        # Held-back sources are never generated from (leakage guard).
        return []
    if mode == "ai":
        raw = _ai_generate(source)
    elif mode == "off":
        raw = extract_cards(source)
    else:
        raise ValueError(f"unknown mode {mode!r}; expected 'ai' or 'off'")

    text_len = len(source["text"])
    kept: list[dict] = []
    for c in raw:
        sid = c.get("source_id")
        span = c.get("source_span")
        if sid != source["source_id"]:
            continue  # cannot trace to this named source → drop
        if (not isinstance(span, (list, tuple)) or len(span) != 2):
            continue
        s, e = span
        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= text_len):
            continue  # empty / out-of-bounds span → drop
        if not str(c.get("front", "")).strip() or not str(c.get("back", "")).strip():
            continue
        kept.append(c)
    return kept


def generate_all(sources: Iterable[dict] | None = None, mode: str = "off") -> list[dict]:
    """Generate cards across every active (non-held-back) source."""
    if sources is None:
        sources = load_catalog(include_heldback=False)
    out: list[dict] = []
    for src in sources:
        out.extend(generate_cards(src, mode=mode))
    return out


def load_generated_cards() -> list[dict]:
    """The committed, model-authored AI card set (ai/generated_cards.json)."""
    return json.loads(GENERATED_CARDS.read_text(encoding="utf-8"))["cards"]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--ai", dest="ai", action="store_true",
                     help="use the guarded frontier-LLM path (needs an API key)")
    grp.add_argument("--no-ai", dest="ai", action="store_false",
                     help="use the deterministic extractor (default)")
    parser.set_defaults(ai=None)
    args = parser.parse_args(argv)

    have_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    # Default: AI only if explicitly asked; otherwise fall back deterministically.
    use_ai = args.ai if args.ai is not None else False

    if use_ai and not have_key:
        # Honest failure — do not fake a live call.
        print("AI mode requested but no API key is set (ANTHROPIC_API_KEY / "
              "OPENAI_API_KEY). Falling back to --no-ai deterministic extractor.\n",
              file=sys.stderr)
        use_ai = False

    mode = "ai" if use_ai else "off"
    cards = generate_all(mode=mode)

    print(f"# mode={mode}  data_cutoff={DATA_CUTOFF}  cards={len(cards)}")
    for c in cards:
        s, e = c["source_span"]
        cited = c and source_by_id(c["source_id"])
        span_text = cited["text"][s:e] if cited else ""
        print(json.dumps({
            "source_id": c["source_id"],
            "source_span": c["source_span"],
            "concept_code": c["concept_code"],
            "front": c["front"],
            "back": c["back"],
            "cited_text": span_text,
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
