// Which regime KPIs actually track this run's P&L — and which only look like they do.
//
// There are ~20 KPIs at 5 checkpoints. Eyeballing them one at a time is how you end
// up believing the third-best coincidence in a 30-day sample, so this ranks them
// all at once and, more importantly, tells you what the ranking is worth: every
// score is measured against the same score computed on *shuffled* P&L, which is
// the only honest way to know whether the best of fifteen means anything.

/** One session's KPI value and what the run made that day. */
export interface DayPoint {
  date: string;
  x: number;
  net: number;
  trades: number;
  wins: number;
}

export interface Score {
  /** Spearman ρ of the KPI against the day's net. */
  rho: number;
  /** Days in the bottom / top third by KPI value. */
  lo: DayPoint[];
  hi: DayPoint[];
  /** Avg net per day in the top third minus the bottom third — the money answer:
   * "what does a day in the good band pay over a day in the bad one". */
  edge: number;
  /** Win-rate points, top third minus bottom third. */
  winEdge: number;
  /** Share of shuffled P&Ls that beat this |ρ|. Low = the pattern is hard to get
   * by luck. NOT a real p-value — no correction, no independence assumption. */
  luck: number;
  days: number;
}

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const mean = (xs: number[]) => (xs.length ? sum(xs) / xs.length : 0);

function ranks(v: number[]): number[] {
  const n = v.length;
  const idx = v.map((x, i) => [x, i] as const).sort((a, b) => a[0] - b[0]);
  const r = new Array<number>(n);
  for (let i = 0; i < n; ) {
    let j = i;
    while (j + 1 < n && idx[j + 1][0] === idx[i][0]) j++; // ties share the mean rank
    const m = (i + j) / 2 + 1;
    for (let k = i; k <= j; k++) r[idx[k][1]] = m;
    i = j + 1;
  }
  return r;
}

function corr(a: number[], b: number[]): number {
  const n = a.length;
  const ma = mean(a);
  const mb = mean(b);
  let num = 0;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i++) {
    num += (a[i] - ma) * (b[i] - mb);
    da += (a[i] - ma) ** 2;
    db += (b[i] - mb) ** 2;
  }
  return da === 0 || db === 0 ? 0 : num / Math.sqrt(da * db);
}

/** Rank correlation. Ranks, not raw values: the relationship is monotone at best,
 * and one +3k session would otherwise drag a Pearson r around by itself. */
export function rankCorr(xs: number[], ys: number[]): number {
  return corr(ranks(xs), ranks(ys));
}

/** Seeded, so a p-value doesn't flicker every time React re-renders. */
function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const PERMUTATIONS = 500;

/** How often a KPI this good shows up when the P&L is shuffled — i.e. when the
 * KPI is known to be meaningless. This is the whole defence against reading a
 * 30-day sample: the question isn't "is ρ big", it's "is ρ bigger than what
 * noise hands me for free". */
function luckOf(xr: number[], ys: number[], rho: number): number {
  const n = ys.length;
  const rnd = mulberry32(0x5eed);
  const target = Math.abs(rho);
  const shuffled = [...ys];
  let beat = 0;
  for (let p = 0; p < PERMUTATIONS; p++) {
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    if (Math.abs(corr(xr, ranks(shuffled))) >= target) beat++;
  }
  return beat / PERMUTATIONS;
}

const winRate = (ds: DayPoint[]) => {
  const t = sum(ds.map((d) => d.trades));
  return t ? (sum(ds.map((d) => d.wins)) / t) * 100 : 0;
};

/** Split by KPI value into thirds and score the gap between the outer two. */
export function score(points: DayPoint[]): Score | null {
  if (points.length < 6) return null; // two days a band — there is nothing to rank
  const sorted = [...points].sort((a, b) => a.x - b.x);
  const n = sorted.length;
  const k = Math.floor(n / 3);
  const lo = sorted.slice(0, k);
  const hi = sorted.slice(n - k);

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.net);
  const rho = rankCorr(xs, ys);

  return {
    rho,
    lo,
    hi,
    edge: mean(hi.map((d) => d.net)) - mean(lo.map((d) => d.net)),
    winEdge: winRate(hi) - winRate(lo),
    luck: luckOf(ranks(xs), ys, rho),
    days: n,
  };
}

/** With this many KPIs on the board, one of them clearing a 1-in-20 bar is what
 * you should EXPECT from pure noise. The bar has to move with the family size —
 * this is the Bonferroni line, which is blunt but errs the safe way. */
export const luckThreshold = (kpiCount: number) => 0.05 / Math.max(1, kpiCount);

/** Roughly how many KPIs should clear a plain 5% bar by chance alone. */
export const expectedFalsePositives = (kpiCount: number) => 0.05 * kpiCount;
