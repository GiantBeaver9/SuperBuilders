#!/usr/bin/env python3
"""Generate a ready-to-import starter deck so you can study flashcards with the
Novel-item Gate add-on immediately, instead of authoring cards by hand.

Every note is tagged `concept::<code>` — the tag the add-on keys on — and there
are enough concepts (>= 9, in strata of 3) that arm assignment populates all
three arms (gate / nogate / vanilla). Content is neutral general-knowledge Q&A;
swap in your own once you've seen the flow.

    python3 scripts/make_starter_deck.py        # -> samples/starter_deck.apkg

Import it in Anki: File -> Import -> pick the .apkg. Then study the "SuperBuilders
Starter" deck; open the dashboard from the Novel-item Gate menu.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from anki.collection import Collection, ExportAnkiPackageOptions

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "starter_deck.apkg"

# (outline code, concept name, [(front, back), ...])
CONCEPTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("1A.1", "European capitals", [
        ("Capital of France?", "Paris"),
        ("Capital of Spain?", "Madrid"),
        ("Capital of Poland?", "Warsaw")]),
    ("1A.2", "Asian capitals", [
        ("Capital of Japan?", "Tokyo"),
        ("Capital of Thailand?", "Bangkok"),
        ("Capital of South Korea?", "Seoul")]),
    ("1B.1", "African capitals", [
        ("Capital of Kenya?", "Nairobi"),
        ("Capital of Egypt?", "Cairo"),
        ("Capital of Morocco?", "Rabat")]),
    ("2A.1", "Major rivers", [
        ("Longest river in the world?", "The Nile"),
        ("Which river flows through London?", "The Thames"),
        ("Which river flows through Cairo?", "The Nile")]),
    ("2A.2", "Mountains", [
        ("Highest mountain on Earth?", "Mount Everest"),
        ("Highest mountain in Africa?", "Kilimanjaro"),
        ("Range separating Europe and Asia?", "The Urals")]),
    ("2B.1", "Oceans", [
        ("Largest ocean?", "The Pacific"),
        ("Ocean between Africa and Australia?", "The Indian Ocean"),
        ("Smallest ocean?", "The Arctic")]),
    ("3A.1", "Chemical elements", [
        ("Symbol for gold?", "Au"),
        ("Symbol for sodium?", "Na"),
        ("Symbol for iron?", "Fe")]),
    ("3A.2", "The planets", [
        ("Largest planet?", "Jupiter"),
        ("Closest planet to the Sun?", "Mercury"),
        ("Which planet has the most prominent rings?", "Saturn")]),
    ("3B.1", "Human body", [
        ("Largest organ of the human body?", "The skin"),
        ("How many bones in the adult human body?", "206"),
        ("Which organ pumps blood?", "The heart")]),
    ("4A.1", "Basic physics", [
        ("Unit of force?", "The newton"),
        ("Speed of light (approx, km/s)?", "300,000 km/s"),
        ("Force that pulls objects toward Earth?", "Gravity")]),
]


def build(out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        col = Collection(os.path.join(td, "collection.anki2"))
        basic = col.models.by_name("Basic")
        n_cards = 0
        for code, name, cards in CONCEPTS:
            did = col.decks.id(f"SuperBuilders Starter::{code} {name}")
            for front, back in cards:
                note = col.new_note(basic)
                note["Front"] = front
                note["Back"] = back
                note.tags = [f"concept::{code}"]
                col.add_note(note, did)
                n_cards += 1
        opts = ExportAnkiPackageOptions(
            with_scheduling=False, with_media=False, legacy=True
        )
        col.export_anki_package(out_path=str(out_path), options=opts, limit=None)
        col.close()
        return n_cards


if __name__ == "__main__":
    n = build(OUT)
    print(f"wrote {OUT}  ({len(CONCEPTS)} concepts, {n} cards)")
