# Provenance of `vendor/sm20`

## What this is

The `sm20/` module from [melpomenex/Incrementum](https://github.com/melpomenex/Incrementum),
vendored verbatim and rebuilt as a standalone crate. It implements SuperMemo's
**Algorithm Arena** — the scheduler SM-20 ships, which commits a weighted blend
of five candidate intervals rather than any single algorithm's answer.

- Upstream commit: `a62923fea0976b4629ded35d046a370b402280f5`
- Upstream licence: Apache-2.0
- Copied from: `src-tauri/src/algorithms/sm20/*.rs`
- Parity fixture copied from: `src/shared/sm20ArenaParityFixture.json`

## Why vendored rather than ported

The module is self-contained: it imports `serde`, `rand`, its own submodules,
and nothing else. It has exactly one `crate::` reference and that is inside a
doc comment. Porting ~3,000 lines of decompiled numerics to Python would mean
re-deriving Delphi `Real48` rounding and 80-bit x87 constants by hand, and every
transcription error would be silent. Compiling the original and shelling out to
it costs one `cargo build` and cannot drift.

## Changes made to the vendored source

Deliberately minimal, so a future re-vendor is a re-copy plus these two edits:

1. `mod.rs` renamed to `src/lib.rs` (crate root rather than a submodule).
2. `SM20CollectionState` gained `#[derive(Debug, Clone, Serialize, Deserialize)]`.
   Upstream serialises the struct field-by-field at its single call site; we
   round-trip the whole thing through a subprocess. Every field already derived
   both traits, so this adds no new constraint.
3. The test's `include_str!` path now points at the fixture's new location.

`src/main.rs` is ours and is not vendored code.

## The lineage caveat, stated plainly

The Arena has **no published specification**. This code was produced by
reverse-engineering `sm20.exe` (Ghidra decompilation, cross-checked with Frida
injection against the running binary). Its ancestor project `sm18-re` was
DMCA'd by SuperMemo World on 2026-05-22.

Incrementum's own licence is Apache-2.0 and that is what this copy relies on,
but the upstream provenance is not something an Apache header settles. This is
vendored for private use inside a personal trading journal. Do not redistribute
it, and do not assume the constants are licensed merely because the wrapper is.

## What is and isn't verified

The module carries a language-neutral golden fixture that pins one full review:
five candidates `[75, 44, 48, 53, 40]` at weights `[6, 14, 45, 25, 10]` blending
to a committed interval of 49 days. `cargo test` in this directory replays it.

Not verified here: the decompilation itself. We have no `sm20.exe` to check the
constants against, so "faithful to SuperMemo" rests on upstream's testimony.
What our tests prove is that *this copy behaves identically to upstream's copy*.

Note also that `kernel.rs` (25% blend weight) and `model3.rs` (45%) carry no
unit tests upstream — 70% of the committed interval flows through untested
code, covered only indirectly by the end-to-end fixture.
