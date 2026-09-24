// The what-if rows you added yourself, remembered between visits.
//
// The preset ladder is the server's and never travels; this is only the handful
// of brackets you wanted to see that it does not offer. They are a preference
// like the ticket's, and they load the same way: anything missing or malformed
// falls back to an empty list rather than breaking the day view.
//
// They are stored per browser and not per sitting on purpose — "what does 60t /
// 120t look like" is a question you ask of every sitting, not of one.

const KEY = "whatIfRows.v1";

/** One row's exits, in ticks from the entry. Mirrors `api.routers.replays.ScenarioIn`.
 *
 *  `trail` is three-valued and all three mean something: `"as-placed"` keeps
 *  whatever trail was on the ticket, `null` runs without one, an object
 *  replaces it.
 *
 *  `flip` is the one field that is not an exit: it takes the other side of every
 *  entry at the same moment, with the placed legs mirrored across the fill. */
export interface WhatIfTrail {
  dist: number;
  step: number;
  beOnly: boolean;
}

export interface WhatIfSpec {
  stop: number | null;
  target: number | null;
  targetR: number | null;
  trail: WhatIfTrail | "as-placed" | null;
  flip: boolean;
}

export const EMPTY_SPEC: WhatIfSpec = {
  stop: null,
  target: null,
  targetR: null,
  trail: "as-placed",
  flip: false,
};

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Read one stored row, or null if it is not one. The server validates ranges;
 *  this only has to keep junk out of the request. */
function parseSpec(raw: unknown): WhatIfSpec | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  let trail: WhatIfSpec["trail"] = "as-placed";
  if (r.trail === null) trail = null;
  else if (r.trail && typeof r.trail === "object") {
    const t = r.trail as Record<string, unknown>;
    if (!isNum(t.dist)) return null;
    trail = { dist: t.dist, step: isNum(t.step) ? t.step : 0, beOnly: !!t.beOnly };
  } else if (r.trail !== undefined && r.trail !== "as-placed") return null;
  const spec: WhatIfSpec = {
    stop: isNum(r.stop) ? r.stop : null,
    target: isNum(r.target) ? r.target : null,
    targetR: isNum(r.targetR) ? r.targetR : null,
    trail,
    // Rows stored before reversal existed are rows that were not reversed.
    flip: !!r.flip,
  };
  // The server refuses both at once; refusing here too keeps a stored row from
  // failing every request until it is found and deleted.
  if (spec.target !== null && spec.targetR !== null) return null;
  return spec;
}

export function loadWhatIfRows(): WhatIfSpec[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.map(parseSpec).filter((s): s is WhatIfSpec => s !== null).slice(0, 20);
  } catch {
    return [];
  }
}

export function saveWhatIfRows(rows: WhatIfSpec[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(rows.slice(0, 20)));
  } catch {
    // A full or blocked localStorage costs the memory, not the feature.
  }
}
