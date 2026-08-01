# Install & use — Novel-item Gate

## 1. Build the add-on package

From the repo root:

```bash
python3 scripts/bundle_addon.py
```

This produces `dist/novel_item_gate.ankiaddon` — a zip containing the aqt layer
(`__init__.py`, `ui/`), the pure engine (`gap/`), and the committed SQL bundled
at `gap/sql/schema` and `gap/sql/queries`. No `aqt` is required to build it.

### How the bundled SQL is found at runtime

`GapDB` runs the committed SQL verbatim; it locates it via
`gap/db.py::_resolve_sql_root`, whose candidate order is:

1. an explicit `sql_root=` argument,
2. the `GAP_SQL_ROOT` environment variable,
3. **a `sql/` directory next to the `gap` package — i.e. `gap/sql/`** ← used inside the installed add-on,
4. (dev only) walking up to the repo root that holds `schema/gap.sql`.

The bundler copies `schema/` → `gap/sql/schema` and `queries/` → `gap/sql/queries`,
so candidate 3 hits and **no environment variable is needed**. `GAP_SQL_ROOT`
stays available as an override.

## 2. Install into Anki

1. Open Anki.
2. **Tools → Add-ons → Install from file…**
3. Choose `dist/novel_item_gate.ankiaddon`.
4. Restart Anki when prompted.

The add-on installs under `addons21/novel_item_gate/` (the `package` name in
`manifest.json`). On restart it:

- puts its own directory on `sys.path` so the bundled `gap` engine imports as a
  top-level package,
- registers the reviewer timing + retirement hooks,
- adds the **Novel-item Gate** menu to the menu bar.

Edit thresholds under **Tools → Add-ons → Novel-item Gate → Config** (see
`config.md`): `novel_gate_threshold` (0.7), `mastery_R_threshold` (0.9),
`abstain_min_attempts` (8).

## 3. Tag notes with concepts

Concept membership is authored as an Anki **note tag** of the form
`concept::<code>`, where `<code>` is the outline code (e.g. `concept::1A.2`).
Tags sync for free over Anki's existing note sync, and the sidecar rebuilds its
derived index from them.

- In the browser, select notes → **Notes → Add Tags…** → enter `concept::1A.2`.
- A note may carry several concept tags; it will map to each concept.
- After tagging, run **Novel-item Gate → Rebuild / assign now** (or just reopen
  the profile) to rebuild the index and assign arms. Concept rows are created
  automatically from the tags; set each concept's exam `weight` and
  `baseline_difficulty` in `gap.db` before first exposure if you are running the
  experiment.

## 4. Open the dashboard

**Novel-item Gate → Open Dashboard.** It loads the bundled web dashboard and is
fed `service.dashboard_payload(mw.col)` as `window.DASHBOARD_DATA` (abstain rule,
coverage %, per-concept performance, endpoints). Concepts below
`abstain_min_attempts` novel attempts show coverage instead of a score.

Other menu actions:

- **Build Points-at-Stake study** — ranks due cards by
  `(card_mastery − novel_accuracy) × exam_weight` and opens them in the browser,
  highest-stake first.
- **Add novel item** — record a novel-item probe attempt (self-graded
  correct/incorrect + latency), written append-only to `gap.novel_revlog`.
- **Rebuild / assign now** — re-run the startup sync on demand.

## 5. Run the offline simulation (crossover)

The pre-registered crossover is validated offline, with no Anki involved — it
runs over a plain SQLite sidecar (`open_sidecar`), which has the math functions
Anki's bundled SQLite lacks:

```bash
python3 sim/simulate.py
```

This seeds a multi-student × concept run, computes the endpoint queries under
`queries/04_endpoints/`, reports the Arm A − Arm B crossover at the committed
exposure bucket (1–4 vs 5+), and writes `dashboard_data.json` for offline
inspection of the same payload the live dashboard renders.
