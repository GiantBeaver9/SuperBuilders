# Traceability — every requirement → code → evidence

Maps each PRD / assessment requirement to the file(s) that implement it and the
command that proves it. Run any "Evidence" command from the repo root.

**Legend:** ✅ implemented & tested here · 🌐 environment-limited (needs a real
machine/devices — a headless Linux CI box cannot build a native mobile GUI or
screen-record the Qt desktop app; the buildable core of each is done and tested).

## Functional requirements

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| **Three separately displayed scores** (memory DOK1, performance DOK2/3, readiness DOK4), each with a confidence range | ✅ | `scoring/scores.py` (`memory_score` FSRS-R + normal CI; `performance_score` Wilson interval; `readiness_score` exam-weighted, coverage-tax CI); surfaced in `addon/ui/web/` dashboard | `python3 tests/test_scoring_calibration.py` |
| **Give-up rule** (no score without sufficient data) | ✅ | `scoring/scores.py` (abstain < 8 novel attempts, documented constants); `queries/05_discipline/abstain_rule.sql` | `python3 eval/leakage_check.py`; test above |
| **Desktop app** (Anki add-on) | ✅ | `addon/` (aqt hooks, novel-item dialog, dashboard webview, menu); installable `.ankiaddon` | `python3 scripts/bundle_addon.py` |
| **Real Rust-level modification to Anki's scheduling/query engine** | ✅ | `rust-fork/` — new `ComputeReadinessGap` `SchedulerService` RPC in `rslib/src/scheduler/readiness.rs` (patch vs Anki 26.05); native FSRS retrievability + points-at-stake ordering | `just test-rust` → **556 tests pass** (552 upstream + 4 new); rationale `docs/RUST_RATIONALE.md` |
| **Python integration test calling the Rust function** | ✅ | `rust-fork/test_readiness_integration.py` (calls the RPC across the protobuf bridge) | `bash rust-fork/assemble_pylib_and_test.sh` |
| **protobuf messaging Rust↔Python↔mobile** | ✅ | `proto/anki/scheduler.proto` (`ConceptGap`/`ConceptStake`) in the fork; `proto/sync_gap.proto` for the sync payload | proto compiles; integration test |
| **Mobile companion app** (AnkiDroid/iOS) | 🌐 | `docs/MOBILE_ARCHITECTURE.md` — integration plan + shared engine; native app build needs the user's mobile toolchain | sync logic below is the loss/dup-critical part, tested |
| **Bidirectional offline sync with reconciliation** | ✅ | `sync/reconcile.py` (append-only grow-only-set CRDT: idempotent/commutative/associative) | `python3 tests/test_sync.py` |
| **Sync integrity** (0 lost / 0 duplicated across 20 cards) | ✅ | `sync/integrity_test.py` (10 phone-offline + 10 desktop) | `python3 sync/integrity_test.py` → 0 lost / 0 dup |
| **AI-generated cards traceable to named sources** | ✅ | `ai/generate.py` (source_id + span per card; untraceable → dropped); `ai/generated_cards.json`; `ai/sources/` | `python3 ai/generate.py` |
| **AI evals beating a simpler baseline** (keyword + vector) | ✅ | `ai/eval_generation.py`, `ai/baselines.py` (grounding + TF-IDF-vs-keyword retrieval) | `python3 ai/eval_generation.py` (AI > baselines on both metrics) |
| **App functions with AI disabled** | ✅ | `ai/generate.py` deterministic `--no-ai` path (runs with no API key) | `python3 ai/generate.py` (default no-AI) |
| **Paraphrase / reword transfer test** (memory vs performance) | ✅ | `eval/paraphrase_test.py` | `python3 eval/paraphrase_test.py` |
| **Coverage map dashboard per exam topic**, tied to an outline | ✅ | `coverage/coverage.py`, `data/outline_lsat.json`; dashboard coverage view | `python3 coverage/coverage.py`; `python3 tests/test_paraphrase_coverage.py` |
| **Crash resilience, zero data corruption** | ✅ | `bench/crash_test.py` (SIGKILL mid-write ×20, WAL + synchronous=FULL) | `python3 bench/crash_test.py` → **20/20 clean** |
| **Ablation test** (feature on/off/baseline Anki) | ✅ | `gap/arms.py` + `gap/retirement.py` (arms: gate/nogate/vanilla); `sim/simulate.py` | `python3 sim/simulate.py` (crossover sign-flip) |

## Performance benchmarks

| Target | Status | Result (this machine) | Where |
|---|---|---|---|
| 50,000-card deck benchmark (median/p95/worst) | ✅ | queue ~420ms, mastery ~220ms | `python3 bench/bench_engine.py` → `bench/RESULTS.md` |
| Dashboard load < 1s / refresh < 500ms | ✅ | **373ms / 370ms** (live tier) | same |
| Session sync < 5s | ✅ | ~9ms on the 20-card set | `sync/integrity_test.py` |
| Zero corrupted collections (20× crash) | ✅ | 20/20 clean | `bench/crash_test.py` |
| Button ack < 50ms / render < 100ms / cold start | 🌐 | GUI-timed — engine-side proxies + method stated | `bench/RESULTS.md` |

## Code quality / evidence

| Requirement | Status | Where |
|---|---|---|
| ≥ 3 Rust unit tests | ✅ | 4 in `rslib/src/scheduler/readiness.rs` |
| Python integration test calling the Rust fn | ✅ | `rust-fork/test_readiness_integration.py` |
| Rationale for why the change belongs in Rust | ✅ | `docs/RUST_RATIONALE.md` |
| Undo works + collection doesn't corrupt | ✅ | RPC is read-only; integration test asserts collection unchanged |
| Reproducible/rerunnable evals with stated cutoffs | ✅ | every `eval/*.py` prints its data cutoff; deterministic |
| Leakage-check scripts | ✅ | `eval/leakage_check.py`, `queries/05_discipline/leakage_check.sql` |
| Calibration (Brier / log-loss) | ✅ | `eval/calibration.py` |
| Traceability table (feature → code → result) | ✅ | this file + `PREREGISTRATION.md` §7 |
| AGPL v3+ license + Anki attribution | ✅ | `LICENSE`, `NOTICE` |
| Clean build on a fresh machine | ✅ | `docs/INSTALL.md`, `rust-fork/README.md`; `scripts/bundle_addon.py` |
| Demo video | 🌐 | `docs/DEMO_SCRIPT.md` (shot list); Qt screen-recording needs the user's machine |

## One-command sanity check

```sh
for t in tests/test_*.py; do python3 "$t" >/dev/null && echo "PASS $t" || echo "FAIL $t"; done
python3 scripts/validate_sql.py        # 10/10 SQL files valid
python3 sim/simulate.py                 # the pre-registered crossover
```
All eight Python suites pass; the Rust suite (`just test-rust`) is 556/556.
