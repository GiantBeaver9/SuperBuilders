# Demo video — shot list

A ~4-minute walkthrough. Everything below runs from the repo; the only parts that
need a real machine are the Anki desktop GUI clips (this repo was built and tested
on a headless CI box, which can't screen-record Qt).

## 0. The thesis (15s)
Show `SPIKY_POV.md` top: *card mastery stops measuring learning past ~4 exposures;
this app gates on novel-item performance instead.* Cut to `PREREGISTRATION.md`'s
prediction table (the committed crossover at exposure 5).

## 1. The result — no Anki needed (45s)
```sh
python3 sim/simulate.py
```
Point at the printed crossover: **−9.7pp at exposures 1–4, +12.3pp at 5+** (the
sign-flip). Then open the dashboard:
```sh
cp sim/dashboard_data.json addon/ui/web/dashboard_data.json
cd addon/ui/web && python3 -m http.server 8000   # open dashboard.html
```
Show the three scores, the abstain rows, the crossover chart, the coverage map.
(Or show the committed `sim/dashboard_preview.png`.)

## 2. The real Rust engine change (60s)
```sh
cd anki-fork && just test-rust        # 556 tests pass (552 + 4 readiness)
bash rust-fork/assemble_pylib_and_test.sh   # Python calls the Rust RPC
```
Show `rust-fork/readiness.rs` (the `ComputeReadinessGap` RPC) and
`docs/RUST_RATIONALE.md`. This is the "real Rust modification" requirement.

## 3. Evidence layer (60s)
```sh
python3 eval/calibration.py       # Brier / log-loss + reliability table
python3 eval/paraphrase_test.py   # memory vs performance transfer gap
python3 ai/eval_generation.py     # AI beats keyword + vector baselines
python3 bench/bench_engine.py     # 50k-card timings vs PRD targets
python3 bench/crash_test.py       # 20/20 crash-clean
python3 sync/integrity_test.py    # 0 lost / 0 duplicated across 20 cards
```

## 4. The desktop add-on in real Anki (45s, needs a machine)
```sh
python3 scripts/bundle_addon.py                 # -> dist/novel_item_gate.ankiaddon
python3 scripts/make_starter_deck.py            # -> samples/starter_deck.apkg
```
In Anki: install the add-on, import the starter deck, study a few cards, open the
**Novel-item Gate → Dashboard**, add a novel item, watch a concept fail the gate.

## 5. Close (15s)
`docs/TRACEABILITY.md` — every requirement mapped to a file + a passing test.
