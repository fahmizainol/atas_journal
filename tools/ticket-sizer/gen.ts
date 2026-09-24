/**
 * Generates the risk-sizer fixture: what `lib/riskSizer.ts` recommends across a
 * grid of vol readings, budgets and contracts, so `tests/test_risk_sizer.py`
 * can hold the arithmetic still.
 *
 * The sizer is the one place in the app where a number the user reads off a
 * chart turns into a contract count, and the two failure modes are both silent:
 * a fee dropped from the denominator sizes you a little too big every time, and
 * a micro priced as a mini sizes you ten times too big once. Neither shows up
 * as a crash. A fixture is how they show up at all.
 *
 * Run it with `tools/ticket-sizer/run.sh`, which bundles this through the
 * esbuild that already ships inside the frontend's vite. Re-run and commit the
 * fixture whenever the rule in `riskSizer.ts` changes on purpose — the Python
 * test failing is the intended alarm when it changes by accident.
 */
import {
  BUDGETS_USD,
  STOP_MULT,
  presetsFor,
  routeLabel,
  stopThatFits,
  type SizerInput,
} from "../../frontend/src/lib/riskSizer";

/** NQ and its micro, as the replay prices them. */
const NQ = { tickUsd: 5.0, commissionPerSide: 3.5 };

/** Readings that matter: the quiet end, the user's own two reference points
 *  (35t where he has sized up, 50t where he sits), the guards' 60t ceiling, and
 *  a hot tape past it. */
const VOLS = [20, 30, 35, 40, 50, 60, 70, 90, 120];

const base: Omit<SizerInput, "volTicks"> = {
  ...NQ,
  maxLossUsd: 2000,
  caps: { minis: 4, micros: 40 },
  stopTicksMax: 60,
};

const rows = [];
for (const volTicks of VOLS) {
  for (const p of presetsFor({ ...base, volTicks })) {
    rows.push({
      volTicks,
      appetite: p.appetite,
      budget: Number(p.budgetUsd.toFixed(2)),
      stopTicks: p.stopTicks,
      overStopCeiling: p.overStopCeiling,
      mini: p.mini && {
        label: routeLabel(p.mini, "NQ", "MNQ"),
        minis: p.mini.minis,
        risk: Number(p.mini.riskUsd.toFixed(2)),
        fees: Number(p.mini.feesUsd.toFixed(2)),
        exposure: Number(p.mini.exposure.toFixed(4)),
        losersToDeath: p.mini.losersToDeath,
      },
      micro: p.micro && {
        label: routeLabel(p.micro, "NQ", "MNQ"),
        micros: p.micro.micros,
        risk: Number(p.micro.riskUsd.toFixed(2)),
        fees: Number(p.micro.feesUsd.toFixed(2)),
        exposure: Number(p.micro.exposure.toFixed(4)),
        losersToDeath: p.micro.losersToDeath,
      },
    });
  }
}

/** The inverse, over the same budgets — what stop a size you insist on can
 *  carry. This is what the panel says when nothing fits. */
const fits = [];
for (const budget of [200, 250, 300, 400, 500]) {
  for (const [minis, micros] of [[1, 0], [2, 0], [4, 0], [0, 10], [0, 20]]) {
    fits.push({
      budget,
      minis,
      micros,
      stopTicks: stopThatFits(budget, minis, micros, NQ.tickUsd, NQ.commissionPerSide),
    });
  }
}

console.log(
  JSON.stringify(
    {
      stopMult: STOP_MULT,
      budgets: BUDGETS_USD,
      contract: NQ,
      base,
      rows,
      fits,
    },
    null,
    2,
  ),
);
