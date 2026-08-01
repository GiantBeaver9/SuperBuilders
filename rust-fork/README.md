# Rust engine change — readiness-gap ordering

A real modification to Anki's Rust scheduling engine (PRD §1): a new
`SchedulerService` RPC, **`ComputeReadinessGap`**, that computes the
points-at-stake / readiness-gap ordering natively, using Anki's own FSRS
retrievability. Rationale, design, and the undo/no-corruption argument are in
[`../docs/RUST_RATIONALE.md`](../docs/RUST_RATIONALE.md).

## Contents

- `readiness-gap.patch` — the complete change (4 files, +224 lines) against Anki
  **26.05** (`ankitects/anki` commit `dc2998f`).
- `readiness.rs` — a readable copy of the new engine module (also in the patch).
- `test_readiness_integration.py` — the Python integration test that calls the
  RPC across the protobuf bridge.

## The change (what the patch touches)

| File | What |
|---|---|
| `proto/anki/scheduler.proto` | new `ComputeReadinessGap` RPC + `ConceptGap`/`ConceptStake` messages |
| `rslib/src/scheduler/readiness.rs` | the native computation + 4 unit tests |
| `rslib/src/scheduler/mod.rs` | registers the module |
| `rslib/src/scheduler/service/mod.rs` | wires the RPC into `SchedulerService for Collection` |

## Apply + build + test on a clean checkout

```sh
git clone --depth 1 --branch 26.05 https://github.com/ankitects/anki.git
cd anki
git apply /path/to/rust-fork/readiness-gap.patch

# Rust unit tests (regenerates protobuf codegen, compiles rslib, runs nextest):
just test-rust
#   -> 556 tests pass (552 upstream + the 4 readiness tests)

# Python integration test (build the wheel, install it, run the test):
just wheels
python -m venv /tmp/forkvenv
/tmp/forkvenv/bin/pip install out/wheels/anki-*.whl
/tmp/forkvenv/bin/python /path/to/rust-fork/test_readiness_integration.py
#   -> OK: Python called the Rust ComputeReadinessGap RPC ...
```

(Requires a recent `just`; the apt package may be too old for Anki's justfile —
`cargo install just` gets a current one.)

## Rust unit tests (in `readiness.rs`)

1. `points_is_gap_times_weight` — the `(mastery − novel) × weight` formula.
2. `retrievability_is_one_at_zero_elapsed_and_decreases` — R via Anki's
   `fsrs::current_retrievability`.
3. `mean_mastery_and_points_compose` — mean-of-R aggregation into the key.
4. `orders_by_gap_and_handles_empty_concepts` — end-to-end through
   `Collection::compute_readiness_gap`.

## Python integration test

`test_readiness_integration.py` builds a real collection, gives cards FSRS state,
calls `col._backend.compute_readiness_gap(...)`, and asserts the returned
mastery, points, and ordering — plus that the collection is unchanged (the RPC is
read-only). This is Python invoking the new Rust function across the bridge.

## Running the integration test in a sandbox (no frontend build)

Anki's `just wheels` runs `yarn install`, which needs network access this
sandbox blocks — but the Python integration test only needs `pylib` + the rust
bridge, both of which `just test-rust` already built. `assemble_pylib_and_test.sh`
assembles those (source `.py` + generated backend + `_rsbridge.so` + a matching
`buildinfo` + `protoc`-generated `*_pb2.py`) and runs the test:

```sh
ANKI_SRC=/path/to/anki-fork bash rust-fork/assemble_pylib_and_test.sh
#   -> OK: Python called the Rust ComputeReadinessGap RPC ...
```

Verified here: **556 Rust tests pass** (552 upstream + 4 readiness) and the
Python integration test prints OK.
