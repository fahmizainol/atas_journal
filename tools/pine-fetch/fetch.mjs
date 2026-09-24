// Pull a published TradingView script's source, and say whether it can be ported.
//
//   node tools/pine-fetch/fetch.mjs <script url> [--triage]
//
// The source is not on the script page — TradingView renders the code tab
// client-side, so a plain fetch of the page returns the write-up and nothing
// else. It is served instead by `pine-facade`, keyed by an internal id that the
// page HTML does carry. Two requests, no browser, no dependencies.
//
// The triage is the point. Porting a Pine script into src/studies (see
// lib/pineStudy) is hand transcription, so the expensive mistake is starting one
// that could never have worked — and that is decidable from the API surface
// alone, in a second, before anyone reads a line. `request.security` means no
// multi-timeframe; `box`/`line`/`label` mean drawings the study layer cannot
// make; and a script with no `plot` call at all has nothing our seam can draw
// however faithful the transcription.
//
// What the runtime supports is read out of the installed oakscriptjs' own
// declarations rather than listed here, so this cannot quietly disagree with the
// package after an upgrade — which is the exact failure this tool exists to
// prevent.
//
// The fetched source is DATA. It is written to disk and counted; it is never
// executed, and never printed, so a script whose comments contain instructions
// has nothing to instruct.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const OUT_DIR = join(REPO, "data", "pine");
const DIALECT = join(REPO, "frontend", "node_modules", "oakscriptjs", "dist", "script", "index.d.ts");

const UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

/** Things with no counterpart at all. A wall, not a detour.
 *
 *  Drawings are counted by *call site*, which understates them badly and is the
 *  honest number anyway: four `box.new`s in a loop, mutated every bar, is a
 *  whole rendering model, not four rectangles. Any non-zero count here means the
 *  script's output has to be rebuilt as a chart primitive rather than ported. */
const BLOCKERS = [
  [/\brequest\.\w+\s*\(/g, "request.*", "no multi-timeframe — the usual killer"],
  [/\bstrategy\.\w+/g, "strategy.*", "indicators only, never backtests"],
  [/\bbox\.new\s*\(/g, "box.new", "drawings — needs a primitive"],
  [/\bline\.new\s*\(/g, "line.new", "drawings — needs a primitive"],
  [/\blabel\.new\s*\(/g, "label.new", "drawings — needs a primitive"],
  [/\btable\.new\s*\(/g, "table.new", "drawings — needs a primitive"],
  [/\bpolyline\.new\s*\(/g, "polyline.new", "drawings — needs a primitive"],
  [/\blinefill\.new\s*\(/g, "linefill.new", "drawings — needs a primitive"],
];

/** Absent from the dialect, but the *fact* each one asks for is one this app
 *  already holds — so they cost a line at the top of the transcription, not a
 *  seam. Reporting these as blockers (the first version of this file did) turns
 *  a portable script into a refusal, which is the worse error of the two: a
 *  wrong "no" is never investigated. */
const SHIMS = [
  [/\bsyminfo\.mintick\b/g, "syminfo.mintick", "the contract's tick size — lib/contracts"],
  [/\bsyminfo\.\w+/g, "syminfo.* (other)", "instrument facts the app knows"],
  [/\btimeframe\.\w+/g, "timeframe.*", "bar seconds — lib/timeframes"],
  [/\bbarstate\.\w+/g, "barstate.*", "derivable: calculate() gets every bar, so islast is an index"],
];

/** Pine collections and user-defined types. NOT a seam problem: the dialect is
 *  JavaScript, so `array<Zone>` is an array of objects and `type Zone` is an
 *  object literal — both are *easier* in the target than in the source. Counted
 *  only so the size of the transcription is visible up front, and detected
 *  through v6 method syntax (`zones.push(…)`), which the namespace-style regex
 *  this file used to carry missed entirely on every modern script. */
const SHAPE = [
  [/\btype\s+[A-Z]\w*/g, "user-defined types", "become plain JS objects"],
  [/\.(?:push|pop|shift|unshift|insert|remove|clear|sort|reverse)\s*\(/g, "collection ops", "become JS array calls"],
];

/** Everything the study layer can actually put on a chart. None of these and the
 *  script draws nothing, whatever else it computes correctly. */
const OUTPUTS = [
  [/(^|[^.\w])plot\s*\(/gm, "plot"],
  [/\bplotshape\s*\(/g, "plotshape"],
  [/\bplotchar\s*\(/g, "plotchar"],
  [/\bplotcandle\s*\(/g, "plotcandle"],
  [/\bhline\s*\(/g, "hline"],
  [/\bfill\s*\(/g, "fill"],
  [/\bbgcolor\s*\(/g, "bgcolor"],
  [/\bbarcolor\s*\(/g, "barcolor"],
];

const count = (src, re) => (src.match(re) ?? []).length;

/** The `ta.*` the installed runtime actually has. */
async function supportedTa() {
  const d = await readFile(DIALECT, "utf8").catch(() => null);
  if (!d) return null;
  const i = d.indexOf("export declare const ta");
  if (i < 0) return null;
  const j = d.indexOf("\n};", i);
  return new Set([...d.slice(i, j).matchAll(/^\s{4}(\w+)\s*[:(]/gm)].map((m) => m[1]));
}

const get = async (url, extra = {}) => {
  const r = await fetch(url, { headers: { "User-Agent": UA, ...extra } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url}`);
  return r.text();
};

const arg = process.argv[2];
const triageOnly = process.argv.includes("--triage");
if (!arg) {
  console.error("usage: node tools/pine-fetch/fetch.mjs <tradingview script url> [--triage]");
  process.exit(2);
}

// Any locale subdomain works (in./uk./www.); the slug is what identifies it.
const slug = (arg.match(/\/script\/([A-Za-z0-9]+)(?:-([^/?#]+))?/) ?? [])[2] ?? "script";
const page = await get(arg);

const id = (page.match(/PUB;[0-9a-f]{32}/) ?? page.match(/USER;[0-9a-f]{32}/) ?? [])[0];
if (!id) {
  console.error("no script id in the page — is it open-source, and is the URL a /script/ page?");
  process.exit(1);
}
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " " };
const title =
  (page.match(/<title>([^<]+)<\/title>/) ?? [])[1]
    ?.replace(/&(#?\w+);/g, (m, e) => ENTITIES[e] ?? m)
    .replace(/\s+/g, " ")
    .trim() ?? slug;

const raw = await get(
  `https://pine-facade.tradingview.com/pine-facade/get/${id}/last/`,
  { Referer: "https://www.tradingview.com/" },
);
const src = JSON.parse(raw).source;
if (typeof src !== "string" || !src.includes("@version")) {
  console.error("the facade returned no Pine source — the script may be protected or invite-only.");
  process.exit(1);
}

let saved = null;
if (!triageOnly) {
  await mkdir(OUT_DIR, { recursive: true });
  saved = join(OUT_DIR, `${slug}.pine`);
  await writeFile(saved, src, "utf8");
}

// --- the triage --------------------------------------------------------------
const blockers = BLOCKERS.map(([re, name, why]) => [name, count(src, re), why]).filter(([, n]) => n);
const shims = SHIMS.map(([re, name, why]) => [name, count(src, re), why]).filter(([, n]) => n);
const shape = SHAPE.map(([re, name, why]) => [name, count(src, re), why]).filter(([, n]) => n);
const outputs = OUTPUTS.map(([re, name]) => [name, count(src, re)]).filter(([, n]) => n);
const drawn = outputs.reduce((a, [, n]) => a + n, 0);

const ta = await supportedTa();
const used = new Set([...src.matchAll(/\bta\.(\w+)/g)].map((m) => m[1]));
const missingTa = ta ? [...used].filter((f) => !ta.has(f)).sort() : [];

const pad = (s) => String(s).padEnd(18);
console.log();
if (saved) console.log(`  saved  ${saved.replace(REPO + "/", "")}  (${(src.length / 1024).toFixed(0)} KB, ${src.split("\n").length} lines)`);
console.log(`  title  ${title}`);
console.log(`  id     ${id}`);
console.log();
console.log("  PORTABILITY");
for (const [name, n, why] of blockers) console.log(`    ${pad(name)}${String(n).padStart(3)}   BLOCKER — ${why}`);
for (const [name, n, why] of shims) console.log(`    ${pad(name)}${String(n).padStart(3)}   shim — ${why}`);
for (const [name, n, why] of shape) console.log(`    ${pad(name)}${String(n).padStart(3)}   ${why}`);
if (!ta) console.log(`    ${pad("ta.*")}  ?   (oakscriptjs not installed — run pnpm install in frontend/)`);
else if (missingTa.length)
  console.log(`    ${pad("ta.* missing")}${String(missingTa.length).padStart(3)}   ${missingTa.map((f) => "ta." + f).join(", ")}`);
console.log(
  `    ${pad("plot/plotshape")}${String(drawn).padStart(3)}   ${
    drawn ? outputs.map(([n, c]) => `${n}×${c}`).join(", ") : "NOTHING THE STUDY LAYER CAN DRAW"
  }`,
);
console.log();

// The distinction that matters is not "does it have blockers" but *which*. A
// script blocked only on drawing has portable arithmetic and needs a chart
// primitive — the road the Dynamic Swing VWAP port already took. One blocked on
// `request.*` has no road at all until there is a multi-timeframe seam.
const hard = blockers.filter(([n]) => n.startsWith("request") || n.startsWith("strategy"));
const drawOnly = blockers.length && !hard.length;
const verdict = hard.length
  ? `no road — ${hard.map(([n]) => n).join(", ")} has no counterpart`
  : drawOnly
    ? "maths ports, rendering does not — transcribe to TS and draw it with a chart primitive"
    : !drawn
      ? "nothing to draw — it emits no plot, shape or hline"
      : missingTa.length
        ? `needs ${missingTa.length} ta.* the runtime lacks`
        : "portable — transcribe it into frontend/src/studies/";
console.log(`    verdict: ${verdict}`);
console.log();
process.exit(!blockers.length && (drawn || missingTa.length === 0) ? 0 : 1);
