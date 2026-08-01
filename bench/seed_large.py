#!/usr/bin/env python3
"""Large offline dataset builder for the engine benchmarks.

Builds a realistic-shape collection entirely offline via
:func:`gap.db.open_sidecar` with ``main_stub=True`` (the fake ``collection.anki2``
schema) plus a populated sidecar ``gap.db``. Everything the benchmarked engine
ops read is seeded here:

  * ``main.notes`` / ``main.cards`` — N cards (one note per card), each card
    carrying FSRS memory state in ``cards.data`` (``{"s": stability, "d": diff}``)
    and every card is a review card (``queue = 2``) so it enters the queue.
  * ``main.revlog`` — 1..6 exposures per card; the newest revlog id is the card's
    last-review instant that FSRS retrievability is measured from.
  * ``gap.concepts`` — M concepts with exam ``weight`` and ``baseline_difficulty``.
  * ``gap.note_concepts`` — every note linked to exactly one concept (round-robin),
    seeded directly (the same rows ``01_open/rebuild_note_concepts.sql`` would
    derive from the ``concept::CODE`` tags, but inserted in bulk for speed).
  * ``gap.novel_items`` / ``gap.novel_item_concepts`` / ``gap.novel_revlog`` —
    practice (``is_holdout = 0``) and held-out (``is_holdout = 1``) novel items with
    attempts, so ``novel_accuracy_by_concept`` and the abstain rule have real data.
  * ``gap.arms`` / ``gap.retirements`` — an arm per concept and a fraction retired.

All inserts go through ``executemany`` inside a single transaction, so a 50k-card
dataset builds in a couple of seconds. The engine never sees how it was built — it
just gets a ``GapDB`` with ``main`` + ``gap`` connected, exactly like the add-on.

Run directly to print how long a build takes:

    python3 bench/seed_large.py            # 50k cards, 200 concepts
    python3 bench/seed_large.py 2000 50    # N cards, M concepts
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Make the repo importable when run as a script (python3 bench/seed_large.py).
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from gap import mastery                      # noqa: E402
from gap.db import GapDB, open_sidecar       # noqa: E402

DAY_MS = 86_400_000
_ARMS = ("gate", "nogate", "vanilla")
_TRIGGER = {"gate": "novel_gate", "nogate": "card_mastery", "vanilla": "anki_default"}


def build_dataset(n_cards: int = 50_000,
                  m_concepts: int = 200,
                  main_path: str | os.PathLike | None = None,
                  gap_path: str | os.PathLike | None = None,
                  seed: int = 1234) -> GapDB:
    """Build an N-card / M-concept dataset and return a query-ready ``GapDB``.

    :param n_cards: number of notes+cards (default 50,000).
    :param m_concepts: number of concepts to spread the cards across.
    :param main_path: on-disk path for the ``main`` (fake collection) db, or
        ``None`` for an in-memory main (fast; used by the benchmark). The crash
        test passes a real file so it can prove the collection stays untouched.
    :param gap_path: on-disk path for the sidecar ``gap.db``; ``None`` mints a
        temp file kept alive for the life of the returned connection.
    :param seed: RNG seed for reproducible content.
    :returns: a :class:`gap.db.GapDB` with ``main`` populated and ``gap`` attached.
    """
    rnd = random.Random(seed)
    if gap_path is None:
        # Kept on disk (not deleted) so the attached connection stays valid; the
        # OS reclaims the temp dir at process exit.
        gap_path = Path(tempfile.mkdtemp(prefix="gapbench_")) / "gap.db"

    gapdb = open_sidecar(main_path, gap_path, main_stub=True)
    con = gapdb.db.con                       # raw sqlite3.Connection (SqliteProxy)

    now = mastery.now_ms()

    # ---- concepts -------------------------------------------------------- #
    concept_rows = [
        (cid,
         f"C{cid}",
         f"Concept {cid}",
         round(1.0 + (cid % 5) * 0.5, 2),           # exam weight 1.0..3.0
         round(2.0 + (cid % 8), 2))                 # baseline_difficulty 2..9
        for cid in range(1, m_concepts + 1)
    ]

    # ---- notes / cards / revlog / note_concepts -------------------------- #
    note_rows: list[tuple] = []
    card_rows: list[tuple] = []
    revlog_rows: list[tuple] = []
    nc_rows: list[tuple] = []

    # Global serial keeps every revlog id unique. It is subtracted (as low-order
    # ms) from a per-card day bucket, so ids are globally distinct AND the card's
    # newest review (its MAX revlog id -> last-review instant) lands ~day_bucket
    # days in the past, giving a real spread of FSRS elapsed times.
    serial = 0
    for i in range(1, n_cards + 1):
        cid = (i % m_concepts) + 1
        guid = f"g{i}"
        code = f"C{cid}"
        note_rows.append(
            (i, guid, 1, now, -1, f" concept::{code} ", "front\x1fback", "front", 0, 0, "")
        )
        stability = round(rnd.uniform(3.0, 400.0), 3)      # days
        difficulty = round(rnd.uniform(1.0, 10.0), 3)
        data = '{"s": %s, "d": %s}' % (stability, difficulty)
        # review card, due today (day-number 0); queue 2 so it enters the queue.
        card_rows.append(
            (i, i, 1, 0, now, -1, 2, 2, 0, max(1, int(stability)),
             2500, 6, 0, 0, 0, 0, 0, data)
        )
        nc_rows.append((guid, cid))
        # 1..6 exposures; the first-inserted row has the largest id -> newest.
        exposures = rnd.randint(1, 6)
        day_bucket = i % 30
        for k in range(exposures):
            serial += 1
            rid = now - day_bucket * DAY_MS - serial
            ease = rnd.randint(1, 4)
            revlog_rows.append(
                (rid, i, -1, ease, 10, 5, 2500, 8000 - k * 500, 1)
            )

    # ---- novel items / attempts ----------------------------------------- #
    ni_rows: list[tuple] = []
    nic_rows: list[tuple] = []
    nr_rows: list[tuple] = []
    item_id = now                       # epoch-ms-style ids, kept globally unique
    rev_id = now + 10 * DAY_MS
    for cid in range(1, m_concepts + 1):
        # 2 practice items (varied attempt counts -> exercise the abstain line) +
        # 1 held-out item per concept.
        for is_holdout, n_attempts in ((0, rnd.randint(1, 12)),
                                       (0, rnd.randint(1, 12)),
                                       (1, rnd.randint(1, 4))):
            item_id += 1
            ni_rows.append((item_id, f"ni{item_id}", f"src{cid}", is_holdout, -1, now))
            nic_rows.append((item_id, cid))
            for _ in range(n_attempts):
                rev_id += 1
                nr_rows.append((rev_id, item_id, rnd.randint(0, 1), rnd.randint(2000, 9000), -1))

    # ---- arms / retirements --------------------------------------------- #
    arm_rows = [
        (cid, _ARMS[cid % 3], now - 60 * DAY_MS)
        for cid in range(1, m_concepts + 1)
    ]
    ret_rows = [
        (cid, now - 5 * DAY_MS, _TRIGGER[_ARMS[cid % 3]])
        for cid in range(1, m_concepts + 1)
        if cid % 4 == 0                         # retire ~a quarter of concepts
    ]

    # ---- one bulk transaction ------------------------------------------- #
    con.execute("BEGIN")
    con.executemany(
        "INSERT INTO gap.concepts(id,code,name,weight,baseline_difficulty)"
        " VALUES(?,?,?,?,?)", concept_rows)
    con.executemany(
        "INSERT INTO notes(id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)", note_rows)
    con.executemany(
        "INSERT INTO cards(id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,"
        "reps,lapses,left,odue,odid,flags,data)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", card_rows)
    con.executemany(
        "INSERT INTO revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type)"
        " VALUES(?,?,?,?,?,?,?,?,?)", revlog_rows)
    con.executemany(
        "INSERT INTO gap.note_concepts(guid,concept_id) VALUES(?,?)", nc_rows)
    con.executemany(
        "INSERT INTO gap.novel_items(id,guid,source_id,is_holdout,usn,mod)"
        " VALUES(?,?,?,?,?,?)", ni_rows)
    con.executemany(
        "INSERT INTO gap.novel_item_concepts(item_id,concept_id) VALUES(?,?)", nic_rows)
    con.executemany(
        "INSERT INTO gap.novel_revlog(id,item_id,correct,time,usn)"
        " VALUES(?,?,?,?,?)", nr_rows)
    con.executemany(
        "INSERT INTO gap.arms(concept_id,arm,assigned_ms) VALUES(?,?,?)", arm_rows)
    con.executemany(
        "INSERT INTO gap.retirements(concept_id,retired_ms,trigger) VALUES(?,?,?)", ret_rows)
    con.commit()

    # Analysis-side read speed: let SQLite plan the big joins well.
    con.execute("ANALYZE")
    con.commit()
    return gapdb


def dataset_stats(gapdb: GapDB) -> dict:
    """Row counts of the seeded dataset (for the benchmark header)."""
    q = lambda sql: gapdb.scalar(sql)
    return {
        "cards": q("SELECT COUNT(*) FROM main.cards"),
        "notes": q("SELECT COUNT(*) FROM main.notes"),
        "revlog": q("SELECT COUNT(*) FROM main.revlog"),
        "concepts": q("SELECT COUNT(*) FROM gap.concepts"),
        "novel_items": q("SELECT COUNT(*) FROM gap.novel_items"),
        "novel_revlog": q("SELECT COUNT(*) FROM gap.novel_revlog"),
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
    m = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    t0 = time.perf_counter()
    g = build_dataset(n_cards=n, m_concepts=m)
    dt = time.perf_counter() - t0
    print(f"built {n} cards / {m} concepts in {dt:.2f}s")
    for k, v in dataset_stats(g).items():
        print(f"  {k:>13}: {v:,}")
