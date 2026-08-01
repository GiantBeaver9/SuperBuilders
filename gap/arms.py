"""Arm assignment — the stratified randomized split into {gate, nogate, vanilla}.

`assign` is a thin wrapper over the committed SQL
(`queries/02_assign/assign_arms.sql`), which stratifies this collection's
concepts by exam-weight and baseline-difficulty terciles and assigns arms
round-robin by a stable code hash. That SQL is math-free (the FSRS `D0(3)`
fallback is a precomputed constant) and `INSERT OR IGNORE`, so it is safe to
run live inside Anki and is idempotent — an already-assigned concept is never
rewritten.

`ensure_concepts_from_tags` is an optional bootstrap helper: it guarantees a
`gap.concepts` row exists for every `concept::<code>` tag found in the
collection, so the index and arm assignment have something to reference. It
only creates the row; the meaningful fields (`weight`, `baseline_difficulty`)
are authored elsewhere, before first exposure.
"""
from __future__ import annotations

from typing import Any


def assign(gapdb: Any) -> dict[str, int]:
    """Assign every unassigned concept to an arm, return {arm: count}.

    Runs `queries/02_assign/assign_arms.sql` (writes only `gap.arms`,
    read-only on `main.*`) then tallies `gap.arms` by arm. Idempotent: the
    SQL is `INSERT OR IGNORE` and only touches concepts absent from
    `gap.arms`, so re-running returns the same totals.
    """
    gapdb.run_file("queries/02_assign/assign_arms.sql")
    gapdb.commit()
    rows = gapdb.all("SELECT arm, COUNT(*) FROM gap.arms GROUP BY arm")
    return {str(arm): int(count) for arm, count in rows}


def arm_of(gapdb: Any, concept_id: int) -> str | None:
    """The arm assigned to `concept_id`, or None if it has not been assigned."""
    return gapdb.scalar("SELECT arm FROM gap.arms WHERE concept_id = ?", concept_id)


def ensure_concepts_from_tags(gapdb: Any) -> int:
    """Insert a `gap.concepts` row for every `concept::<code>` tag missing one.

    Discovers concept codes present in `main.notes.tags` (token form
    `concept::<code>`, tags being space-delimited) and inserts any that are
    absent from `gap.concepts`, with a stable id (next `MAX(id) + 1`),
    `name = code`, and `weight = 1.0`. Returns the number of rows inserted.

    Math-free: tag tokenizing happens in Python; the SQL is plain SELECT /
    INSERT with no math functions, so this is safe to run live inside Anki.
    Idempotent: codes already present are skipped, so a second run inserts 0.

    This only guarantees a concept row *exists* so the index and arm
    assignment can reference it. The authored fields — `weight` and
    `baseline_difficulty` (which drive queue ordering and arm stratification)
    — are set elsewhere, before first exposure; this helper never touches them
    on an existing row.
    """
    existing = set(gapdb.list("SELECT code FROM gap.concepts"))
    found: set[str] = set()
    for tags in gapdb.list("SELECT tags FROM main.notes"):
        for token in (tags or "").split():
            if token.startswith("concept::"):
                code = token[len("concept::"):]
                if code:
                    found.add(code)

    missing = sorted(found - existing)
    if not missing:
        return 0

    next_id = int(gapdb.scalar("SELECT COALESCE(MAX(id), 0) FROM gap.concepts") or 0)
    inserted = 0
    for code in missing:
        next_id += 1
        gapdb.execute(
            "INSERT INTO gap.concepts (id, code, name, weight) VALUES (?, ?, ?, ?)",
            next_id, code, code, 1.0,
        )
        inserted += 1
    gapdb.commit()
    return inserted
