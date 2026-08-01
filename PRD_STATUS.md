# PRD coverage tracker

Living map of every PRD requirement → status → where it lives. Updated as the
build loops. Honesty is a graded requirement here, so environment limits are
stated, not hidden.

**Legend:** ✅ done & verified · 🟡 partial / in progress · ⬜ not started ·
🌐 environment-limited (needs the user's machine/devices — headless Linux cloud
box can't build/run native mobile GUIs or record the Anki Qt app)

## 0. Foundation already built (pre-this-PRD)

| Item | Status | Where |
|---|---|---|
| Spiky POV / thesis | ✅ | `SPIKY_POV.md` |
| Pre-registration (arms, endpoints, kill criteria, cutoffs) | ✅ | `PREREGISTRATION.md` |
| `gap.db` append-only sidecar schema | ✅ | `schema/gap.sql` |
| Engine (index/arms/queue/retirement/novel/mastery/stats/service) | ✅ | `gap/`, tested on real Anki |
| Ablation arms (gate / nogate / vanilla) | ✅ | `gap/arms.py`, `gap/retirement.py` |
| Analysis endpoints + leakage SQL | ✅ | `queries/04_endpoints`, `queries/05_discipline` |
| Simulation reproducing the crossover | ✅ | `sim/simulate.py` |
| Anki add-on (Python/aqt layer) | ✅ | `addon/` |
| Dashboard (standalone + webview) | ✅ | `addon/ui/web/` |

## 1. Rust engine modification — CRITICAL (PRD's load-bearing requirement)

| Requirement | Status | Plan / where |
|---|---|---|
| Real modification to Anki's Rust scheduling/query engine | 🟡 | Fork building at `/home/user/anki-src`; change targets `rslib/src/scheduler` or `search` |
| ≥ 3 Rust unit tests | ⬜ | in the new rslib module |
| Python integration test calling the Rust function | ⬜ | via `anki` backend RPC |
| Documented rationale for why it belongs in Rust | ⬜ | `docs/RUST_RATIONALE.md` |
| Undo works + collection doesn't corrupt (proof) | ⬜ | Rust test + Python undo test |
| protobuf messaging Rust↔Python | ⬜ | new RPC in `proto/anki/*.proto` |

## 2. Three separated scores + DOK framing

| Requirement | Status | Plan / where |
|---|---|---|
| Memory score (DOK 1) + confidence range | 🟡 | FSRS retrievability = memory; needs explicit score + CI |
| Performance score (DOK 2/3) on novel items + CI | 🟡 | novel accuracy; needs score + CI |
| Readiness score (DOK 4) projected exam score + CI | ⬜ | model mapping memory+performance+coverage → projected score |
| Give-up / abstain rule (no score without data) | ✅ | abstain rule (< 8 novel attempts) |
| Honestly expose the gap between the three | 🟡 | dashboard shows points-at-stake gap; extend to 3-score panel |

## 3. Calibration & evidence

| Requirement | Status | Plan / where |
|---|---|---|
| Memory-model calibration (80%→80%) via Brier/log-loss | ⬜ | `eval/calibration.py` on held-back reviews |
| Performance prediction accuracy on held-back items | ⬜ | `eval/performance_pred.py` |
| Reproducible/rerunnable evals w/ stated data cutoffs | 🟡 | pre-reg has cutoffs; formalize eval harness |
| Leakage-check scripts (no contamination) | 🟡 | `queries/05_discipline/leakage_check.sql`; add runnable `eval/leakage_check.py` |
| Paraphrase / reword transfer test (memory vs performance) | ⬜ | `eval/paraphrase_test.py` |

## 4. AI card generation

| Requirement | Status | Plan / where |
|---|---|---|
| AI-generated cards traceable to named sources | ⬜ | `ai/generate.py` (source_id already in schema) |
| Evals beating a simpler baseline (keyword / embedding) | ⬜ | `ai/eval_generation.py` vs keyword + embedding baselines |
| App functions with AI disabled | ⬜ | deterministic fallback path + flag |
| Uses a frontier LLM | 🟡 | Anthropic API (available here) instead of OpenAI — noted swap |

## 5. Coverage map

| Requirement | Status | Plan / where |
|---|---|---|
| Coverage map dashboard per exam topic | 🟡 | per-concept dashboard exists; add topic rollup |
| Tied to an official exam outline | ⬜ | pick one exam (LSAT/MCAT), encode outline → `data/outline_*.json` |

## 6. Mobile companion + two-way sync

| Requirement | Status | Plan / where |
|---|---|---|
| Phone app (AnkiDroid/iOS), full-featured companion | 🌐 | can't build/run native app headlessly; deliver architecture + AnkiDroid integration plan + shared engine |
| Bidirectional offline sync with reconciliation | 🟡🌐 | implement + test the reconciliation LOGIC headlessly (`sync/`), protobuf contract |
| Sync integrity (0 lost/dup across 20 cards) | 🟡 | headless reconciliation test harness (10 phone-offline + 10 desktop) |

## 7. Performance benchmarks & resilience

| Requirement | Status | Plan / where |
|---|---|---|
| 50,000-card deck benchmark (median/p95/worst) | ⬜ | `bench/bench_engine.py` on a 50k seeded collection |
| Dashboard load < 1s / refresh < 500ms | 🟡 | measure `stats.dashboard_payload` build time |
| Session sync < 5s | 🟡 | measure reconciliation on the 20-card set |
| Zero corrupted collections in 20 crash tests | 🟡 | `bench/crash_test.py` (kill mid-write, reopen, verify) |
| Button ack p95 < 50ms / render p95 < 100ms / cold start | 🌐 | GUI-timed; provide engine-side proxies + method |

## 8. Licensing, build, deliverables

| Requirement | Status | Plan / where |
|---|---|---|
| AGPL v3+ license | 🟡 | `LICENSE` (this commit) |
| Attribution to Anki (+ BSD components) | 🟡 | `NOTICE` (this commit) |
| Clean build on a fresh machine | 🟡 | `scripts/bundle_addon.py` + `docs/INSTALL.md`; add fresh-machine doc |
| Traceability table (feature/POV → code → measurable result) | 🟡 | `PREREGISTRATION.md` §7; expand in `docs/TRACEABILITY.md` |
| Source code | 🟡 | this repo |
| Demo video | ⬜🌐 | `docs/DEMO_SCRIPT.md` + rendered dashboard assets; screen-recording the Qt app needs the user's machine |

## Loop order

1. **Rust** (critical, in flight): prove build → real change → 3 unit tests → Python integration → rationale + undo proof.
2. **Evidence layer** (parallel, buildable here): three-scores + calibration, AI gen + evals, paraphrase/leakage, coverage map, benchmarks/crash/sync-logic.
3. **Packaging & honesty**: AGPL/NOTICE, traceability, fresh-build doc, demo script, mobile architecture.
