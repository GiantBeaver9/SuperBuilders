"""Novel items — the objects Anki has no notion of.

A *novel item* is a passage/question the student has never seen, used to probe
transfer. It is NOT an Anki card and must never enter `main.revlog`; it lives
entirely in the sidecar (`gap.novel_items` / `gap.novel_item_concepts` /
`gap.novel_revlog`). Timestamps are epoch milliseconds, mirroring Anki's id
convention.

Two invariants this module enforces (from the pre-registration):

* **`is_holdout` is set at INSERT and never updated.** Practice items
  (`is_holdout = 0`) drive the retirement gate and the queue; held-out items
  (`is_holdout = 1`) are the terminal contrast. The two are kept strictly
  separate — every read here filters on the flag.
* **`gap.novel_revlog` is append-only.** `record_attempt` only ever INSERTs.

All SQL here is math-free (plain reads, `COUNT`, `GROUP BY`), so every function
is safe to run live inside Anki.
"""
from __future__ import annotations

from typing import Any

from gap.mastery import now_ms


def add_item(gapdb: Any, guid: str, source_id: str, is_holdout: bool,
             concept_ids: list[int], mod_ms: int | None = None) -> int:
    """Insert a novel item and its concept links; return the item id.

    The id is `mod_ms` (when given, for reproducible seeding) else now in
    epoch ms, and doubles as the `mod` stamp. `is_holdout` is written here and
    never changed by a later update. Each concept in `concept_ids` becomes a
    `gap.novel_item_concepts` link (a passage can hit several concepts).
    """
    item_id = mod_ms if mod_ms is not None else now_ms()
    gapdb.execute(
        "INSERT INTO gap.novel_items (id, guid, source_id, is_holdout, mod) "
        "VALUES (?, ?, ?, ?, ?)",
        item_id, guid, source_id, 1 if is_holdout else 0, item_id,
    )
    for concept_id in concept_ids:
        gapdb.execute(
            "INSERT OR IGNORE INTO gap.novel_item_concepts (item_id, concept_id) "
            "VALUES (?, ?)",
            item_id, concept_id,
        )
    gapdb.commit()
    return item_id


def record_attempt(gapdb: Any, item_id: int, correct: bool, time_ms: int,
                   at_ms: int | None = None) -> int:
    """Append one novel attempt (grade + latency); return the new revlog id.

    Append-only: this only INSERTs into `gap.novel_revlog`. The id is `at_ms`
    (when given) else now in epoch ms; `time_ms` is the latency, in the same
    units as `main.revlog.time`.
    """
    revlog_id = at_ms if at_ms is not None else now_ms()
    gapdb.execute(
        "INSERT INTO gap.novel_revlog (id, item_id, correct, time) "
        "VALUES (?, ?, ?, ?)",
        revlog_id, item_id, 1 if correct else 0, time_ms,
    )
    gapdb.commit()
    return revlog_id


def next_item(gapdb: Any, concept_id: int, holdout: bool = False) -> int | None:
    """Pick the novel item for `concept_id` with the FEWEST prior attempts.

    Restricted to items whose `is_holdout` matches `holdout`. Ties on attempt
    count are broken by lowest id. Returns the item id, or None if the concept
    has no matching item. Math-free (`COUNT` in an ORDER BY subquery, no math
    functions), so it is safe live.
    """
    return gapdb.scalar(
        """
        SELECT ni.id
        FROM gap.novel_items ni
        JOIN gap.novel_item_concepts nic ON nic.item_id = ni.id
        WHERE nic.concept_id = ? AND ni.is_holdout = ?
        ORDER BY
          (SELECT COUNT(*) FROM gap.novel_revlog nr WHERE nr.item_id = ni.id) ASC,
          ni.id ASC
        LIMIT 1
        """,
        concept_id, 1 if holdout else 0,
    )


def attempt_counts(gapdb: Any, holdout: bool = False) -> dict[int, int]:
    """concept_id -> number of novel attempts, for the given holdout flag.

    Counts `gap.novel_revlog` rows joined to concepts through
    `gap.novel_item_concepts`, filtered to items with the matching
    `is_holdout`. Practice (`holdout=False`) and held-out (`holdout=True`)
    counts are strictly separate. Math-free.
    """
    rows = gapdb.all(
        """
        SELECT nic.concept_id, COUNT(*)
        FROM gap.novel_revlog nr
        JOIN gap.novel_items ni          ON ni.id = nr.item_id
        JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
        WHERE ni.is_holdout = ?
        GROUP BY nic.concept_id
        """,
        1 if holdout else 0,
    )
    return {int(concept_id): int(count) for concept_id, count in rows}
