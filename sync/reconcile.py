"""Two-way merge of two `gap.db` sidecars that diverged offline.

The whole point of the sidecar's design (see `schema/gap.sql` and
`docs/ENGINE.md`) is that the measurement tables are **append-only** with
**epoch-millisecond primary keys**. That single decision turns offline sync from
a hard distributed-systems problem into a near-trivial one: reconciliation is a
*set union* with a couple of documented, order-independent conflict rules.

Why append-only + epoch-ms ids make this conflict-light and safe
----------------------------------------------------------------
A review, a novel attempt, a novel item: each is created once, on one device, at
one instant, and never mutated afterwards. Its primary key is the creation
instant in epoch milliseconds. So:

* **A row present in both sidecars is the *same* row** — the same creation event,
  carried into both files by a prior sync. Merging it is a no-op, never a
  duplicate. (This is the load-bearing assumption: ids are creation-time, not
  per-device sequence numbers, so two devices never mint the same id for two
  different events.)
* **No row is ever edited**, so there is no "which edit wins" question for the
  append-only tables at all. The merge of two append-only logs is their union.

Union of sets is **idempotent** (A ∪ A = A), **commutative** (A ∪ B = B ∪ A) and
**associative** ((A ∪ B) ∪ C = A ∪ (B ∪ C)). That is exactly the algebra of a
state-based CRDT (a grow-only set, "G-Set"). Reconciliation therefore needs no
clock, no coordination, no version vectors, and no primary — it is deterministic
and gives the same converged state no matter the order or number of times
devices sync.

The two non-append-only tables (`arms`, `retirements`) hold **one row per
concept**, so they are the only places a genuine conflict can arise. Both are
resolved by a **least-value merge** (a bounded-lattice join), which is *also*
idempotent/commutative/associative:

* `arms`      — keep the **earliest `assigned_ms`**. Arm assignment must predate
  first exposure (pre-registration); the earliest assignment is the authoritative
  one, and a later "assignment" can only be a re-derivation of the same fact.
* `retirements` — keep the **earliest `retired_ms`**. Retirement does not
  un-happen; the first time the rule fired is when the concept retired.

Ties (identical timestamp, differing payload — which the design says cannot
happen for a given concept, but we resolve anyway for total determinism) are
broken by the lexicographically smaller payload (`arm` / `trigger`), so the merge
stays commutative even in that impossible corner.

`concepts`, `note_concepts`, `novel_item_concepts` and `meta` are likewise merged
by union on their primary keys (idempotent; a shared row is the same row).

Public API
----------
* `reconcile(db_a_path, db_b_path, out_path)` — produce a NEW merged sidecar
  (A ∪ B) at `out_path`, leaving A and B untouched.
* `reconcile_into(target_path, source_path)`  — merge `source` INTO `target`
  in place (the device-side "apply what the peer sent me" operation).
* `row_snapshot(path)` — `{table: set(rows)}`, a value-equality view used by the
  tests to assert commutativity and 0-lost/0-dup.

No wall-clock is read anywhere in this module; the result is a pure function of
the two inputs.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from typing import Dict, Set, Tuple

# --------------------------------------------------------------------------- #
# Table registry — the merge rules, declared as data.
# --------------------------------------------------------------------------- #

# Append-only / reference tables: merged by UNION on their primary key. A row
# that already exists (same PK, and by the epoch-ms design, the same content) is
# ignored — never duplicated. `SELECT *` is safe because both files share the
# committed schema, so column order is identical.
UNION_TABLES: Tuple[str, ...] = (
    "meta",                 # schema version etc. — keep whatever is present.
    "concepts",             # authored reference data, INTEGER PK id.
    "note_concepts",        # PK (guid, concept_id).
    "novel_items",          # PK id (epoch ms); guid also UNIQUE.
    "novel_item_concepts",  # PK (item_id, concept_id).
    "novel_revlog",         # PK id (epoch ms) — the attempts. Append-only log.
)

# One-row-per-concept tables: least-timestamp wins, tie broken by payload.
#   table -> (pk_column, timestamp_column, payload_column)
EARLIEST_TABLES: Dict[str, Tuple[str, str, str]] = {
    "arms":        ("concept_id", "assigned_ms", "arm"),
    "retirements": ("concept_id", "retired_ms", "trigger"),
}


# --------------------------------------------------------------------------- #
# Core in-place merge.
# --------------------------------------------------------------------------- #
def reconcile_into(target_path: str | os.PathLike, source_path: str | os.PathLike) -> None:
    """Merge every row of the `source` sidecar into `target`, in place.

    Deterministic and clock-free. Union tables gain any rows they were missing
    (existing rows are left exactly as they are — INSERT OR IGNORE). The
    one-per-concept tables (`arms`, `retirements`) are resolved to the earliest
    timestamp per concept via an UPSERT whose guard only overwrites when the
    incoming row is strictly earlier (or equal-but-lexicographically-smaller
    payload, for total determinism). Runs in a single transaction.
    """
    con = sqlite3.connect(os.fspath(target_path))
    try:
        con.execute("ATTACH DATABASE ? AS src", (os.fspath(source_path),))
        con.execute("BEGIN")
        for table in UNION_TABLES:
            # Grow-only-set union: pull in rows this side has never seen.
            con.execute(f"INSERT OR IGNORE INTO main.{table} SELECT * FROM src.{table}")
        for table, (pk, ts, payload) in EARLIEST_TABLES.items():
            # Least-timestamp lattice join. New concepts insert; conflicting
            # concepts overwrite ONLY when the incoming row is authoritative
            # (strictly earlier, or an impossible exact tie with a smaller
            # payload — kept for commutativity).
            con.execute(
                f"""
                INSERT INTO main.{table} SELECT * FROM src.{table} WHERE true
                ON CONFLICT({pk}) DO UPDATE SET
                    {ts}      = excluded.{ts},
                    {payload} = excluded.{payload}
                WHERE excluded.{ts} < {table}.{ts}
                   OR (excluded.{ts} = {table}.{ts}
                       AND excluded.{payload} < {table}.{payload})
                """
            )
        con.execute("COMMIT")
        con.execute("DETACH DATABASE src")
    finally:
        con.close()


def reconcile(db_a_path: str | os.PathLike,
              db_b_path: str | os.PathLike,
              out_path: str | os.PathLike) -> str:
    """Produce a NEW merged sidecar `A ∪ B` at `out_path`; A and B are untouched.

    Implemented as: copy A verbatim (preserving A's schema, indexes and every
    row), then `reconcile_into` B. Because the merge is commutative, the choice
    of A as the base does not affect the converged row set — `reconcile(a, b)`
    and `reconcile(b, a)` yield value-equal databases. Returns `out_path`.
    """
    out = os.fspath(out_path)
    shutil.copyfile(os.fspath(db_a_path), out)   # full copy: schema + all A rows
    reconcile_into(out, db_b_path)
    return out


# --------------------------------------------------------------------------- #
# Verification helper (used by the tests / integrity harness).
# --------------------------------------------------------------------------- #
def row_snapshot(path: str | os.PathLike) -> Dict[str, Set[Tuple]]:
    """Return `{table: set(all rows)}` for every merged table — a value view.

    Two sidecars with equal snapshots have converged. `meta` is included last
    but excluded from equality-sensitive comparisons by callers that only care
    about measurement data; here we simply report every table.
    """
    con = sqlite3.connect(os.fspath(path))
    try:
        snap: Dict[str, Set[Tuple]] = {}
        for table in UNION_TABLES + tuple(EARLIEST_TABLES):
            snap[table] = set(con.execute(f"SELECT * FROM {table}").fetchall())
        return snap
    finally:
        con.close()
