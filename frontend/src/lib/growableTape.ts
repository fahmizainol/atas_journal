// A tape that is still arriving.
//
// `Tape` (lib/replayEngine) is already the right shape for this: `n` is separate
// from the typed arrays' lengths, so a tape can have capacity beyond its row
// count, and nothing downstream reads `.length`. `ReplayEngine.advance()` re-reads
// `tape.n` on every iteration and holds a live reference to the tape *object*, so
// a tape that grows needs no engine change at all — which is exactly why the
// replay/live seam was cut above the engine (lib/tapeSource) rather than inside it.
//
// So this adds one thing: decoding a delta block from `/live/tape` straight into
// the tail. Not into a temporary Tape that is then copied — the block arrives as
// the same self-contained delta encoding a whole session does (api/tape_codec),
// and prefix-summing it directly into the tail at `n` is one pass instead of two.
//
// THE ONE RULE FOR CONSUMERS. Read the arrays through the tape object at the
// moment you use them (`tape.level[i]`), never hoist one into a ref or a closure
// that outlives the call. Growth reallocates, and a hoisted array would go on
// serving the rows it had when it was captured. Every current consumer already
// does this — the chart passes `tape.level` as an argument at call time, the sim
// indexes `tape.t[i]` — and appends only happen in the poll callback, so a
// synchronous read can never interleave with one.

import type { Tape } from "./replayEngine";

/** One block from `GET /live/tape` — rows [since, next) of the live tape.
 *
 *  Self-contained: its own `t0`/`price0`, opening with `dt[0] === dp[0] === 0`,
 *  so it decodes without reference to what came before it. */
export interface TapeBlock {
  gen: string;
  /** The caller's row indices belonged to a session that is gone. Drop them. */
  reset: boolean;
  since: number;
  /** The row this block ends at — the next cursor. Advance on this, NOT on
   *  `rows`, which is the server's row count at reply time and generally runs
   *  ahead of the block. */
  next: number;
  rows: number;
  closed: boolean;
  n: number;
  t0: number;
  dt: number[];
  price0: number;
  dp: number[];
  size: number[];
  side: string;
}

export interface GrowableTape extends Tape {
  /** Prefix-sum a block onto the tail. Returns the new row count. */
  append(block: TapeBlock): number;
  /** Rows of context sitting in front of the live ones — 0 unless prior days
   *  were seeded in. The live session begins at this index, so it is both the
   *  count of live rows (`n - ctx`) and the row the engine's session starts at. */
  readonly ctx: number;
}

/** A session's worth of headroom. NQ runs ~0.3-1M prints over the 18:00→18:00
 *  day, so this holds a busy one without ever reallocating; the doubling below
 *  is the safety net, not the plan. */
const INITIAL_CAPACITY = 1 << 20;

/**
 * A growing tape, optionally with whole prior sessions already on the front.
 *
 * `context` is the days to draw to the left of the session, oldest first. They
 * are copied in at construction and the live rows land behind them, which is the
 * one ordering that keeps **tick indices stable for the life of the session**:
 * an order's `idx` and the ladder's snapshots are positions in this array, so
 * context that arrived later and shifted everything right would silently
 * renumber every fill already recorded. Seeding is therefore a decision made
 * once, before the first block — the caller waits for the days rather than
 * splicing them in afterwards.
 *
 * Nothing downstream needs to know. `ReplayEngine` already binary-searches its
 * session start (`session_start_ms`) against the tape it is handed and draws
 * whatever sits before it as context bars — the same path the Simulator's
 * `concatTapes` feeds.
 */
export function createGrowableTape(
  tickSize: number,
  pointValue: number,
  context: readonly Tape[] = [],
  capacity = INITIAL_CAPACITY,
): GrowableTape {
  const ctx = context.reduce((a, t) => a + t.n, 0);
  while (capacity < ctx) capacity *= 2;
  const tape: GrowableTape = {
    n: 0,
    t: new Float64Array(capacity),
    price: new Float64Array(capacity),
    level: new Int32Array(capacity),
    size: new Int32Array(capacity),
    side: new Uint8Array(capacity),
    tickSize,
    pointValue,
    ctx,
    append(block: TapeBlock): number {
      const k = block.n;
      if (k <= 0) return this.n;
      grow(this, this.n + k);
      let accT = block.t0;
      let accTk = Math.round(block.price0 / this.tickSize);
      const { dt, dp, size: sz, side: sd } = block;
      const at = this.n;
      for (let i = 0; i < k; i++) {
        accT += dt[i]; // dt[0] === 0
        accTk += dp[i]; // dp[0] === 0
        const j = at + i;
        this.t[j] = accT;
        this.price[j] = accTk * this.tickSize;
        this.level[j] = accTk;
        this.size[j] = sz[i];
        const c = sd.charCodeAt(i);
        this.side[j] = c === 65 ? 1 : c === 66 ? 2 : 0; // 'A' : 'B' : else
      }
      // Published last, like the server's own row count: anything that reads `n`
      // is then guaranteed the rows behind it are written.
      this.n = at + k;
      return this.n;
    },
  };
  let at = 0;
  for (const p of context) {
    tape.t.set(p.t.subarray(0, p.n), at);
    tape.price.set(p.price.subarray(0, p.n), at);
    tape.level.set(p.level.subarray(0, p.n), at);
    tape.size.set(p.size.subarray(0, p.n), at);
    tape.side.set(p.side.subarray(0, p.n), at);
    at += p.n;
  }
  tape.n = ctx;
  return tape;
}

function grow(tape: GrowableTape, need: number): void {
  let cap = tape.t.length;
  if (need <= cap) return;
  while (cap < need) cap *= 2;
  tape.t = copyInto(new Float64Array(cap), tape.t, tape.n);
  tape.price = copyInto(new Float64Array(cap), tape.price, tape.n);
  tape.level = copyInto(new Int32Array(cap), tape.level, tape.n);
  tape.size = copyInto(new Int32Array(cap), tape.size, tape.n);
  tape.side = copyInto(new Uint8Array(cap), tape.side, tape.n);
}

function copyInto<T extends { set(a: ArrayLike<number>, o?: number): void }>(
  next: T,
  prev: { subarray(a: number, b: number): ArrayLike<number> },
  n: number,
): T {
  next.set(prev.subarray(0, n));
  return next;
}
