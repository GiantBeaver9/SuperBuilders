# Engine benchmark — 50k-card deck

- **Machine:** Linux x86_64, 4 logical core(s), Python 3.11.15 (headless cloud box)
- **N cards:** 50,000  ·  **concepts:** 200  ·  **repetitions:** 15
- **Dataset:** 50,000 cards, 175,171 revlog rows, 600 novel items, 3,069 novel attempts
- **Dataset build (cold seed):** 1.25s

| op | median (ms) | p95 (ms) | worst (ms) | best (ms) |
|---|---:|---:|---:|---:|
| `queue` | 422.2 | 435.0 | 448.4 | 415.9 |
| `dashboard_load` | 3666.8 | 3748.1 | 3766.2 | 3577.3 |
| `dashboard_refresh` | 3699.7 | 3763.3 | 3829.1 | 3566.0 |
| `mastery` | 222.3 | 238.3 | 239.6 | 218.9 |

### `dashboard_load` component breakdown (median ms)

| component | median (ms) |
|---|---:|
| `card_mastery` | 221.1 |
| `coverage_query` | 136.0 |
| `endpoint:crossover` | 1258.6 |
| `endpoint:terminal` | 0.7 |
| `endpoint:throughput` | 182.5 |
| `endpoint:latency` | 1782.8 |

The core dashboard (card mastery + coverage) is ~357ms; the four research-analysis **endpoint** blocks add ~3225ms, dominated by the exposure-index joins in `primary_crossover` and `latency_dissociation`. Those endpoint SQL files live in `queries/` (read-only here); lazy-loading them behind the panel would bring the first paint under the 1s budget — but this run measures the real, unmodified `dashboard_payload` in full.

## PRD §7 targets (this machine)

- **[FAIL]** dashboard load (payload build) median 3666.8ms >= 1000ms target  — target: dashboard load < 1s
- **[FAIL]** dashboard refresh (2nd payload build) median 3699.7ms >= 500ms target  — target: dashboard refresh < 500ms

## Scope / honesty

These are **engine-side** timings: the Python cost of building each payload against SQLite on a headless Linux cloud box. They are the part of the < 1s / < 500ms budget this repo owns.

They are **not** the PRD's GUI button-ack (< 50ms) or render (< 100ms) numbers — those are Qt/webview-timed and must be measured on the user's own Anki install; the webview render sits on top of the payload-build time reported here.

`dashboard_refresh` is a second `dashboard_payload` call (warm SQLite cache), which is what a user re-opening the panel triggers — no result is memoised, so it re-runs the full build.

<!-- crash-test-results -->

# Crash / corruption-resilience test

- **Iterations:** 20 (SIGKILL the sidecar writer at a random mid-write instant)
- **Base dataset:** 2,000 cards / 50 concepts on disk
- **Journal mode:** WAL (synchronous=FULL)
- **Corrupted sidecars:** 0 / 20
- **Rounds where the kill landed mid-write (attempts committed before the crash):** 14 / 20
- **`collection.anki2` untouched every round:** True

## Result: PASS — 20/20 clean

Each novel attempt is a single atomic transaction (item + concept link + revlog). A kill mid-transaction rolls the whole attempt back on the next open, so the sidecar only ever holds complete attempts — verified by the orphan/half-write/duplicate checks above. The collection stays byte-for-byte identical because the writer never opens it: the sidecar is a physically separate file.
