#!/usr/bin/env bash
# Build + assemble the fork's pylib and run the Python integration test.
#
# On a normal machine `just wheels` builds the installable wheel and you just
# `pip install` it (see README.md). In a sandbox where the frontend build is
# blocked (Anki's `just wheels` runs `yarn install`, which needs network access
# to repo.yarnpkg.com), the Python integration test does NOT need the TS/Svelte
# frontend at all — only pylib + the rust bridge. This script assembles exactly
# that from the artifacts `just test-rust` already produced, and runs the test.
#
# Usage:  ANKI_SRC=/path/to/anki-fork  bash rust-fork/assemble_pylib_and_test.sh
set -euo pipefail

ANKI_SRC="${ANKI_SRC:-/home/user/anki-src}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FP="${FP:-/tmp/forkpylib}"

BUILDHASH="$(cat "$ANKI_SRC/out/buildhash")"
echo "fork buildhash: $BUILDHASH"

rm -rf "$FP"; mkdir -p "$FP/anki"
# 1. hand-written pylib source
cp -r "$ANKI_SRC"/pylib/anki/* "$FP/anki/"
# 2. overlay generated: backend bindings, fluent, and the rust bridge .so
#    (these come from the `just test-rust` build and contain the new RPC)
cp "$ANKI_SRC"/out/pylib/anki/_backend_generated.py \
   "$ANKI_SRC"/out/pylib/anki/_fluent.py \
   "$ANKI_SRC"/out/pylib/anki/_rsbridge.so "$FP/anki/"
# 3. buildinfo matching the bridge's build hash
cat > "$FP/anki/buildinfo.py" <<EOF
buildhash = "$BUILDHASH"
version = "$(cat "$ANKI_SRC/.version" 2>/dev/null || echo 26.05)"
EOF
# 4. protobuf python bindings from the fork's .proto (includes ConceptGap etc.)
protoc -I "$ANKI_SRC/proto" --python_out="$FP" "$ANKI_SRC"/proto/anki/*.proto

echo "running integration test..."
PYTHONPATH="$FP" python3 "$REPO/rust-fork/test_readiness_integration.py"
