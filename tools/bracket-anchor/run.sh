#!/usr/bin/env bash
# Regenerate the bracket-anchor fixture from the real `lib/replaySim.ts` and
# the fill model it runs under.
#
#   tools/bracket-anchor/run.sh
#
# Same shape as tools/ticket-sizer/run.sh, and for the same reason: bundle
# through the esbuild that already ships inside the frontend's vite (no new
# dependency, no test runner to install) and write the result to
# tests/fixtures/bracket_anchor.json. Commit the fixture with whatever change to
# `replaySim.ts` moved it — an uncommitted regeneration is how the alarm gets
# silenced instead of answered.
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

mkdir -p "$root/tests/fixtures"
node "$tmp/gen.mjs" > "$root/tests/fixtures/bracket_anchor.json"
echo "wrote tests/fixtures/bracket_anchor.json"
