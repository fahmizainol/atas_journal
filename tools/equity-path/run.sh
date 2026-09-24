#!/usr/bin/env bash
# Assert the sitting's equity path against the real `lib/replaySim.ts`.
#
#   tools/equity-path/run.sh
#
# Same shape as tools/bracket-anchor/run.sh: bundle through the esbuild that
# already ships inside the frontend's vite (no new dependency, no test runner to
# install) and run the assertions in node. Unlike that one this writes no
# fixture — it either passes or it names what broke.
#
# What it protects is the two properties an intraday-trailing account's floor
# rests on: that the peak is a fold a rewind can lower, and that it marks on
# prints so an unrealised high leaves a trace. See gen.ts.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
esbuild="$root/frontend/node_modules/.bin/esbuild"

if [ ! -x "$esbuild" ]; then
  echo "esbuild not found at $esbuild — run 'pnpm install' in frontend/ first." >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

"$esbuild" "$here/gen.ts" \
  --bundle --format=esm --platform=node --log-level=warning \
  --outfile="$tmp/gen.mjs"

node "$tmp/gen.mjs"
