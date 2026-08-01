# Engine architecture & contract

The add-on splits in two: a **pure engine** (`gap/`, no GUI deps, tested against a
real headless Anki collection) and a thin **aqt layer** (`addon/`). This doc is
the contract every engine module builds on. Two shared foundations are already
built and verified — do not reimplement them:

## 1. Database access — `gap/db.py`

`GapDB` wraps a DB proxy and runs the committed SQL (`schema/gap.sql` and the ten
files under `queries/`) verbatim. The proxy is Anki's own `col.db` in the add-on,
or `SqliteProxy` (a `sqlite3` wrapper with the same methods) in sim/tests.

```python
from gap.db import GapDB, open_sidecar, ensure_sidecar_schema, attach_gap

# sim / tests (plain sqlite3, math functions available):
g = open_sidecar(main_path_or_None, gap_path, main_stub=True)   # main_stub seeds a fake collection
# real add-on (Anki backend):
ensure_sidecar_schema(gap_path); attach_gap(col.db, gap_path); g = GapDB(col.db)

g.run_file("queries/01_open/rebuild_note_concepts.sql")   # execute every statement
rows = g.query("queries/03_queue/points_at_stake.sql")    # single-SELECT file -> rows
sets = g.query_all("queries/04_endpoints/primary_crossover.sql")  # multi-SELECT -> list of row-lists
g.all(sql, *args); g.scalar(sql, *args); g.list(sql, *args); g.execute(sql, *args)
```

Rules that do not bend:
- **Read-only on `main.*`.** Writes go only to `gap.*`.
- **The schema lives in the sidecar file**, applied via `ensure_sidecar_schema`
  (a dedicated connection). Never run `schema/gap.sql` through `col.db` — the
  unqualified `CREATE`s would land in the collection.
- **Timestamps are epoch milliseconds** (Anki's id unit).
- Qualify tables `main.` / `gap.` in SQL. No bind parameters inside the `.sql`
  files (bind through `run_file(..., params=...)` only if unavoidable).

## 2. The Anki SQLite math limitation — this drives the whole design

Anki's bundled SQLite has **no math functions**: `pow`, `exp`, `sqrt`, `ln`,
`log`, `power` are all missing (verified, anki 26.05). It *does* have
`json_extract`, `unixepoch`, `ntile`, `abs`, `round`, and `*` arithmetic.

Therefore:
- **Live queries** (anything the add-on runs through `col.db` — `01_open`,
  `02_assign`, and any ranking/retirement SQL) **must be math-free.** `02_assign`
  already is (the `D0(3)` fallback is a precomputed constant).
- **Retrievability is computed in Python** — `gap/mastery.py`. Fetch raw card
  state with math-free SQL, compute `R` in Python. Never call `pow`/`sqrt` in a
  query that runs live.
- **Offline analysis queries** (`04_endpoints/*`, `05_discipline/*`) may use SQL
  math freely — they run in the researcher's environment (`sqlite3`/DuckDB, which
  *have* the math functions). The simulation runs them over a plain SQLite
  connection (`open_sidecar`), never through Anki.

## 3. Retrievability & accuracy — `gap/mastery.py` (built, verified)

```python
from gap import mastery
mastery.retrievability(stability, elapsed_days)         # FSRS-5 R in (0,1]
mastery.card_mastery_by_concept(gapdb, at_ms=None)      # concept_id -> mean R
mastery.novel_accuracy_by_concept(gapdb, holdout=False) # concept_id -> mean correct (practice/held-out)
```
Python `R` is pinned equal to the SQL `pow` result by a test — keep it that way.

## 4. Module map (who builds what)

| Module | Responsibility |
|---|---|
| `gap/db.py` ✅ | DB proxy, SQL runner, schema, attach — **done** |
| `gap/mastery.py` ✅ | FSRS retrievability + accuracy in Python — **done** |
| `gap/index.py` | Rebuild `note_concepts` from tags (runs `01_open`) |
| `gap/arms.py` | Assign arms (runs `02_assign`) |
| `gap/queue.py` | Points-at-stake ranking (Python mastery + novel accuracy × weight) |
| `gap/retirement.py` | Evaluate each arm's rule, persist `gap.retirements` |
| `gap/novel.py` | Novel-item CRUD + attempt logging (grade+latency → `novel_revlog`) |
| `gap/stats.py` | Dashboard payload: abstain rule, coverage, per-concept perf, endpoints |
| `addon/` | aqt hooks, novel dialog, dashboard webview, manifest/config |
| `sim/simulate.py` | Seeded multi-student × concept run → endpoints → crossover + `dashboard_data.json` |

Every module: docstring, type hints, no GUI import in `gap/*`, and a test under
`tests/` that runs against a real headless `Collection` (or `open_sidecar` for
offline-math parts). Live-path SQL stays math-free.
