import { useQuery } from "@tanstack/react-query";

import { apiGet } from "../lib/api";

/**
 * The dealer-gamma regime for the running session.
 *
 * Fetched **once per session, not per tick**, and that is a property of the data
 * rather than a shortcut: open interest is published by the OCC pre-open and then
 * frozen, so the whole net-GEX-vs-spot curve is fixed for the day and the only
 * thing that moves is where price sits on it. `gexAt` below is the per-tick half,
 * and it is an interpolation over 241 points — no request, no options feed.
 *
 * Half-hourly refetch is for the case where the collector's cron lands *after*
 * the page was opened (the box reboots unpredictably and the job is hourly), not
 * because the book changes. A tab left open overnight picks up the new day.
 */
const GEX_POLL_MS = 30 * 60 * 1000;

export type GexBook = {
  sym: string;
  book_date: string;
  cdn_stamp: string;
  is_pre_open: boolean;
  ref: number;
  /** [ratio, netGexInBillions] — spot as a fraction of the book's reference close. */
  curve: [number, number][];
  at_ref: number;
  flip: number | null;
  flip_r: number | null;
  call_wall: number | null;
  call_wall_r: number | null;
  put_wall: number | null;
  put_wall_r: number | null;
  n_live: number;
  note: string;
};

export type GexRegime =
  | { available: false; reason: string; book?: GexBook }
  | {
      available: true;
      symbol: string;
      date: string;
      book: GexBook;
      px_ref: number;
      px_ref_date: string;
      px_ref_source: string;
      flip_px: number | null;
      call_wall_px: number | null;
      put_wall_px: number | null;
      implied_basis: number;
      implied_basis_pct: number;
      stale_days: number;
    };

export function useGexRegime(symbol: string | null, date: string | null) {
  return useQuery({
    queryKey: ["live", "gex", symbol, date],
    queryFn: () => apiGet<GexRegime>("/live/gex", { symbol, date }),
    refetchInterval: GEX_POLL_MS,
    enabled: !!symbol && !!date,
    // A missing book is an ordinary outcome, not a failure to retry at.
    retry: false,
  });
}

/**
 * Net GEX ($B) at a futures price, via the ratio the book was served in.
 *
 * `null` outside the ±6% grid rather than a clamped edge value: past that the
 * book has essentially no open interest left, and an edge value would render as
 * a confident zero — which reads as "neutral regime" when it actually means
 * "off the map".
 */
export function gexAt(curve: [number, number][], px: number, pxRef: number): number | null {
  if (!curve.length || !pxRef) return null;
  const r = px / pxRef;
  if (r < curve[0][0] || r > curve[curve.length - 1][0]) return null;
  for (let i = 0; i < curve.length - 1; i++) {
    const [ra, ga] = curve[i];
    const [rb, gb] = curve[i + 1];
    if (r >= ra && r <= rb) {
      const span = rb - ra;
      return span === 0 ? ga : ga + ((r - ra) / span) * (gb - ga);
    }
  }
  return null;
}
