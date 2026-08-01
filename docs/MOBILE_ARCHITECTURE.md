# Mobile companion & two-way offline sync — architecture

This is the honest architecture for PRD §6 (phone companion + bidirectional
offline sync with reconciliation). It states plainly what is **built and tested
here** versus what **requires the user's mobile toolchain/devices** and is
therefore out of scope for this headless Linux build box.

## What is built and tested here (no devices needed)

| Piece | File | Status |
|---|---|---|
| Reconciliation core (the merge) | [`sync/reconcile.py`](../sync/reconcile.py) | ✅ built, tested |
| PRD §6 20-card integrity harness | [`sync/integrity_test.py`](../sync/integrity_test.py) | ✅ PASS: 0 lost / 0 duplicated |
| Rust↔Python↔mobile wire contract | [`proto/sync_gap.proto`](../proto/sync_gap.proto) | ✅ compiles with `protoc` |
| Property tests (idempotent/commutative/associative/earliest-wins) | [`tests/test_sync.py`](../tests/test_sync.py) | ✅ prints OK |

The reconciliation LOGIC — the only part that can silently lose or duplicate a
review — is complete, deterministic, and verified against the PRD's exact
scenario. The native app shell that hosts it is what needs a phone.

## The core idea: an append-only sidecar that rides alongside Anki's own sync

Anki already syncs `collection.anki2` (notes, cards, revlog) through AnkiWeb.
This project's data — novel items, novel attempts, arm assignments, retirements —
lives in a **separate `gap.db` sidecar** (see [`schema/gap.sql`](../schema/gap.sql))
that sits *beside* `collection.anki2`. That placement is deliberate: an AnkiWeb
full download replaces `collection.anki2` wholesale, and the sidecar survives it
untouched (documented in `PREREGISTRATION.md`'s SQL header and `gap/db.py`).

Because the sidecar is separate, **it needs its own sync path** — and that path
is `sync/reconcile.py`. The design that makes this safe is the schema itself:

* Every measurement table is **append-only** with an **epoch-millisecond primary
  key**. A row is created once, on one device, at one instant, and never edited.
* Therefore a row present on two devices is *the same row*, and merging two
  sidecars is a **set union** — a grow-only-set CRDT. Union is idempotent,
  commutative, and associative, so sync converges with **no leader, no locks, no
  version vectors, and no clock**, in any order, any number of times.
* The only two non-append-only tables (`arms`, `retirements`) are one-row-per-
  concept and merge by **earliest-timestamp-wins**, a bounded-lattice join with
  the same three algebraic properties.

This is why the offline story is genuinely easy here and hard elsewhere: the hard
part (conflict resolution) was designed out at the schema level, not patched in
at the sync layer. `sync/reconcile.py`'s module docstring works through the
argument in full.

## Offline-first flow (what actually happens on reconnect)

```
  Phone (offline)                 Desktop (offline)
  ┌────────────────┐              ┌────────────────┐
  │ shared Rust    │              │ shared Rust    │
  │ gap engine     │              │ gap engine     │
  │  → phone.gap.db│              │  → desktop.gap.db
  │ +10 attempts   │              │ +10 attempts   │
  └───────┬────────┘              └───────┬────────┘
          │        both reconnect         │
          └──────────────┬────────────────┘
                         ▼
         Exchange UsnCursor (high-water marks),
         then swap SidecarBatch payloads (proto/sync_gap.proto).
         Each side calls reconcile_into(local, peer_batch).
                         ▼
         Both converge to the identical union:
         20 attempts, each exactly once. 0 lost, 0 duplicated.
```

Each device keeps writing to its own local `gap.db` while offline. On reconnect
the two exchange batches and call `reconcile_into` (or `reconcile` to a fresh
file). Convergence is guaranteed by the CRDT properties, so it does not matter
who initiates, whether one side syncs twice, or whether a batch is a full
snapshot or a USN-bounded delta.

## AnkiDroid integration plan

AnkiDroid is the Android Anki client. It embeds the same **Rust backend**
(`rslib`, exposed over a protobuf RPC / JNI bridge) that the desktop uses — which
is exactly why the engine can be shared rather than reimplemented per platform.

1. **Where the sidecar attaches.** AnkiDroid opens its collection through the
   Rust backend's `Collection`/`CollectionBuilder`, which owns the rusqlite
   handle to `collection.anki2`. The sidecar attaches at the same layer the
   desktop add-on uses (`gap/db.py::attach_gap` → `ATTACH DATABASE 'gap.db' AS
   gap`): immediately after the backend opens the collection, `gap.db` in the
   same directory is attached onto that connection, and the schema is applied to
   the sidecar file first (`ensure_sidecar_schema`) so the `CREATE`s never land
   in `collection.anki2`. On Android the collection dir is app-private storage
   (`AnkiDroid/collection.media/..`), and `gap.db` sits beside `collection.anki2`
   there.

2. **Where the shared Rust engine is reused.** The gap engine's live path is
   intentionally math-free SQL plus a thin Python/Rust mastery calc (see
   `docs/ENGINE.md` §2). Ported into `rslib` as a `gap` module, it exposes the
   same operations to both the desktop `aqt` add-on and the AnkiDroid Kotlin UI
   through the existing backend RPC — one engine, two front-ends. The
   `GapSyncEngine` service in `proto/sync_gap.proto` is the boundary that Rust
   implements and both front-ends call across their respective FFIs (PyO3 on
   desktop, JNI on Android).

3. **Offline-first + reconciliation on reconnect.** AnkiDroid already runs
   offline and syncs the collection to AnkiWeb when connected. The sidecar sync
   runs on the same reconnect trigger: after the collection sync settles, the
   device exchanges `SyncHello`/`UsnCursor`, pulls the peer's `SidecarBatch`,
   and applies it with the reconciliation core (`reconcile_into`). Transport can
   piggyback on the same channel the collection uses or a companion endpoint;
   the merge is transport-agnostic because it is a pure function of two files.

## The protobuf contract

[`proto/sync_gap.proto`](../proto/sync_gap.proto) is the Rust↔Python↔mobile
contract the PRD calls for. It defines one row message per sidecar table
(mirroring `schema/gap.sql`, `usn`/`mod` carried per row), a `SidecarBatch`
snapshot/delta, a `UsnCursor` for delta sync, and the `GapSyncEngine` service
(`Hello`/`Pull`/`Push`). `SyncApplyResult` carries the exact counters the
integrity test asserts, including `rows_duplicated` and `rows_lost`, which are 0
by construction. It compiles cleanly with `protoc` (checked in
`tests/test_sync.py::test_proto_compiles`).

## Honest scope statement — what needs the user's machine

**Building and running the native Android/iOS app is out of scope for this
headless build box.** There is no Android SDK/NDK, no Gradle/Xcode toolchain, no
emulator, and no physical device here, so the GUI shell cannot be compiled or
launched, and a device-to-device sync cannot be demonstrated end-to-end on this
machine. What is delivered instead is the part that carries the correctness risk:
the reconciliation core, its contract, and a headless test that reproduces the
PRD's 20-card scenario exactly and shows 0 lost / 0 duplicated.

Concrete steps to complete it on the user's mobile toolchain:

1. **Clone AnkiDroid** and set up Android Studio + SDK/NDK (per AnkiDroid's
   `CONTRIBUTING`).
2. **Vendor the `gap` Rust module** into the `rslib` the AnkiDroid backend
   builds, exposing it over the existing backend RPC.
3. **Attach the sidecar** at collection-open in the backend (step 1 above),
   guarded so the `CREATE`s land in `gap.db`, never `collection.anki2`.
4. **Generate the proto bindings** — `protoc` the committed
   `proto/sync_gap.proto` for Kotlin (Android), Rust (`prost`/`tonic`), and
   Python (desktop), so all three share one wire type set.
5. **Wire the reconnect hook**: after collection sync, run the
   `Hello`/`Pull`/`Push` exchange and call the reconciliation core
   (`reconcile_into`) — the same logic already tested in `sync/`.
6. **Re-run the integrity scenario on-device**: 10 attempts on the phone while in
   airplane mode + 10 on desktop, reconnect, and confirm 20 attempts, 0 lost / 0
   duplicated — the mobile mirror of `sync/integrity_test.py`.

The reconciliation core those steps plug into is already the working, tested
`sync/reconcile.py`.
