# Novel-item Gate — an Anki add-on that refuses to call familiarity "learning"

> **Every spaced-repetition app ships a rising line and calls it learning. Past
> four exposures, that line is measuring familiarity — not knowledge. This is
> the app that refuses to believe it — and pre-registers the exact point where
> the consensus metric detaches from reality.**

Full argument: **[`SPIKY_POV.md`](SPIKY_POV.md)**. The falsifiable, committed-before-data
experiment: **[`PREREGISTRATION.md`](PREREGISTRATION.md)**.

## What this is

A real Anki add-on that gates concept retirement on **novel-item accuracy**, not
card mastery — plus the full measurement rig to test whether that gate actually
buys durable learning:

- **Novel-item retirement gate** — a concept retires only when you can answer
  questions you've never seen (≥ 0.70), not when a card score ceilings.
- **Points-at-stake queue** — ranks due cards by `(card_mastery − novel_accuracy) × exam_weight`,
  i.e. by the size of the gap between what your cards imply and what novel items show.
- **Three-arm experiment** — `gate` (full app) / `nogate` (ablation) / `vanilla`
  (unmodified Anki), assigned per concept before first exposure.
- **Abstaining dashboard** — refuses a Performance score below 8 novel attempts,
  shows coverage % instead of faking confidence.
- **Append-only sidecar** (`gap.db`) beside `collection.anki2` — never writes
  `revlog`/`cards`/`notes`, survives an AnkiWeb download.

## How to start

### 1. See the result right now — no Anki install needed

```sh
python3 sim/simulate.py
```
Runs a deterministic 3-students × 40-concepts simulation and prints the endpoint
summary — the pre-registered **crossover sign-flip** (Arm A worse at exposures
1-4, better at 5+), throughput cost, and latency dissociation — then writes
`sim/dashboard_data.json`. *(Simulated data, engineered to exercise the analysis
pipeline end-to-end — labelled as such, not an empirical result.)*

View that result in the dashboard, standalone in a browser:
```sh
cp sim/dashboard_data.json addon/ui/web/dashboard_data.json
cd addon/ui/web && python3 -m http.server 8000
# open http://localhost:8000/dashboard.html
```
(A rendered preview is committed at [`sim/dashboard_preview.png`](sim/dashboard_preview.png).)

### 2. Run the real add-on in Anki

```sh
python3 scripts/bundle_addon.py          # -> dist/novel_item_gate.ankiaddon
```
Then in Anki: **Tools → Add-ons → Install from file…** → pick the `.ankiaddon`.
Tag the notes for each concept with `concept::<code>` (e.g. `concept::1A.2`),
restart, and use the **Novel-item Gate** menu (Open Dashboard / Build
Points-at-Stake study / Add novel item / Rebuild & assign). Full walkthrough:
[`docs/INSTALL.md`](docs/INSTALL.md).

### 3. Run the tests (against a real Anki backend)

```sh
pip install anki          # backend only; the add-on itself needs no install
python3 tests/test_index_arms_novel.py
python3 tests/test_queue_retirement.py
python3 tests/test_stats.py
python3 scripts/validate_sql.py           # offline analysis SQL (10 files)
```

## How it's built

Two layers. The **engine** (`gap/`) is pure Python with no GUI dependency, so it
runs and is tested against a real headless Anki collection; the **add-on**
(`addon/`) is the thin `aqt` layer. A hard constraint shaped the design: Anki's
bundled SQLite has **no math functions** (`pow`/`exp`/`sqrt`), so live
retrievability is computed in Python (`gap/mastery.py`) and pinned equal to the
analysis SQL by a test. See **[`docs/ENGINE.md`](docs/ENGINE.md)**.

| Path | What's there |
|---|---|
| `gap/` | Engine: `db`, `mastery`, `index`, `arms`, `queue`, `retirement`, `novel`, `stats`, `service` |
| `addon/` | Anki add-on: `__init__`, `ui/{hooks,menu,novel_dialog,dashboard}`, `ui/web/` dashboard, `manifest`/`config` |
| `schema/` | `gap.sql` (the sidecar) + `main_stub.sql` (validation stand-in) |
| `queries/` | 10 SQL files, one per lifecycle group (open · assign · queue · endpoints · discipline) |
| `sim/` | `simulate.py` → the crossover + `dashboard_data.json` |
| `scripts/` | `bundle_addon.py`, `validate_sql.py`, `e2e_seed_test.py` |
| `tests/` | Engine tests against real Anki + dashboard render check |
| `docs/` | `ENGINE.md`, `INSTALL.md` |
