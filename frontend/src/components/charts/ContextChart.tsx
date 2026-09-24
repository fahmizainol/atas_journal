import { useMemo } from "react";
import { useTradeContextChart } from "../../hooks/useTrades";
import type { FilterScope } from "../../lib/queryKeys";
import type { PriceLineSpec, TradeRect } from "../../lib/chartTypes";
import { CandlestickChart } from "./CandlestickChart";

/** The trade's own window: half an hour either side of it, and nothing else.
 *
 * The trade replay (`TradeReplay`) already draws this trade on its whole
 * session, and that is the better view for asking *where* in the day it
 * happened. This one exists for the other question — what the approach looked
 * like and what followed — which a full session compresses into a few pixels
 * either side of the fill.
 *
 * It passes no session layers of its own — no anchored VWAPs, no server-built
 * profiles, no footprint. What it does add are the lines the numbers alone
 * cannot place: the entry level, and the envelope the following thirty minutes
 * actually traded in.
 *
 * Layers the user has switched on globally still draw here, and that is correct
 * rather than a leak — `CandlestickChart`'s visibility map is deliberately one
 * map across every chart in the app (see STUDY_PANE), on the reasoning that a
 * band you hid because you never read it should stay hidden wherever you meet
 * it. Forcing this one chart bare would make it the exception that teaches you
 * not to trust the toggle.
 */
export function ContextChart({
  scope,
  tradeKey,
}: {
  scope: FilterScope;
  tradeKey: string;
}) {
  const { data, isLoading } = useTradeContextChart(scope, tradeKey, true);

  const rects: TradeRect[] = useMemo(() => {
    if (!data?.entry || !data?.exit) return [];
    const profitable =
      data.direction === "Short"
        ? data.exit.price < data.entry.price
        : data.exit.price > data.entry.price;
    return [
      {
        entry_time: data.entry.time,
        exit_time: data.exit.time,
        entry_price: data.entry.price,
        exit_price: data.exit.price,
        net_pnl: 0,
        profitable,
      },
    ];
  }, [data?.entry, data?.exit, data?.direction]);

  const lines: PriceLineSpec[] = useMemo(() => {
    const c = data?.context;
    if (!c || !data?.exit) return [];
    const sign = data.direction === "Short" ? -1 : 1;
    const out: PriceLineSpec[] = [];
    if (data.entry) {
      out.push({
        price: data.entry.price,
        color: "#8892a6",
        // Whether this level came back is `post_ret_entry_s`; drawing it is what
        // lets you see *how* it came back, or that it never did.
        title: "entry",
      });
    }
    if (c.post_mfe_pts_30m != null) {
      out.push({
        price: data.exit.price + sign * c.post_mfe_pts_30m,
        color: "#3fb950",
        title: `30m best +${c.post_mfe_pts_30m.toFixed(1)}`,
      });
    }
    if (c.post_mae_pts_30m != null) {
      out.push({
        price: data.exit.price + sign * c.post_mae_pts_30m,
        color: "#c0392b",
        // The half that makes the green line honest: holding for it meant
        // sitting through this.
        title: `30m worst ${c.post_mae_pts_30m.toFixed(1)}`,
      });
    }
    return out;
  }, [data?.context, data?.exit, data?.entry, data?.direction]);

  if (isLoading) return <div className="notice">Loading window…</div>;
  if (!data) return null;
  if (!data.bars.length)
    return (
      <div className="notice">
        No cached ticks for this session, so the window can't be drawn. The stored
        numbers above came from a measurement made when the tape was available.
      </div>
    );

  return (
    <CandlestickChart
      bars={data.bars}
      tradeRects={rects}
      priceLines={lines}
      height={260}
    />
  );
}
