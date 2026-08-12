// The pane layouts, checked as arithmetic.
//
//   node tools/layoutcheck.mjs
//
// `lib/paneLayout.ts` is a table of grid line numbers, which is exactly the kind
// of data where a typo produces something that renders — just wrong. A pane
// placed one line off does not throw; it overlaps its neighbour, or leaves a
// strip of background where a chart should be, and you find out by looking.
//
// So this checks the two properties the table has to have and the eye is bad at:
// every layout **tiles** the 2x2 of content cells with no hole and no overlap,
// and every divider sits in the divider track on its own axis. It found a
// transposed `quad` placement the first time it ran.
//
// No browser and no test runner: Node strips the types and imports the module
// the app imports. Run it after touching LAYOUTS.

import {
  LAYOUTS,
  LAYOUT_IDS,
  MAX_PANES,
  clampPaneIndex,
  clampRatio,
  gridArea,
  gridTemplate,
} from "../frontend/src/lib/paneLayout.ts";

let bad = 0;
const fail = (m) => {
  console.log(`  ✗ ${m}`);
  bad++;
};

for (const id of LAYOUT_IDS) {
  const L = LAYOUTS[id];
  if (L.place.length !== L.panes) fail(`${id}: ${L.place.length} places for ${L.panes} panes`);

  // Which of the four content cells each pane covers. A span of lines 1..4
  // crosses the divider track and therefore covers both cells on that axis —
  // which is how the panes that are not split on an axis are written.
  const cells = new Map();
  L.place.forEach((p, i) => {
    const span = (t) => (t[0] === 1 && t[1] === 4 ? [0, 1] : t[0] === 1 ? [0] : [1]);
    for (const r of span(p.row))
      for (const c of span(p.col)) {
        const k = `${r},${c}`;
        if (cells.has(k)) fail(`${id}: panes ${cells.get(k)} and ${i} both cover cell ${k}`);
        cells.set(k, i);
      }
  });
  if (cells.size !== 4) fail(`${id}: covers ${cells.size}/4 cells — a hole in the grid`);

  for (const d of L.dividers) {
    const track = d.axis === "v" ? d.col : d.row;
    if (track[0] !== 2 || track[1] !== 3)
      fail(`${id}: ${d.axis} divider at ${track} is not in the divider track`);
  }
  // Two dividers on one axis would both drag the same ratio.
  const axes = new Set(L.dividers.map((d) => d.axis));
  if (L.dividers.length !== axes.size) fail(`${id}: more than one divider on an axis`);

  console.log(
    `  ✓ ${id.padEnd(6)} ${L.panes} pane(s) · ${L.dividers.length} divider(s) · ` +
      L.place.map(gridArea).join("  |  "),
  );
}

// The helpers, which the page trusts to keep a divider reachable and a pane
// index real.
if (clampRatio(5, 60) !== 20) fail("clampRatio does not hold the low edge");
if (clampRatio(95, 60) !== 80) fail("clampRatio does not hold the high edge");
if (clampRatio("nonsense", 60) !== 60) fail("clampRatio does not fall back");
if (clampPaneIndex(3, "col2") !== 0) fail("clampPaneIndex keeps a pane that no longer exists");
if (clampPaneIndex(1, "col2") !== 1) fail("clampPaneIndex drops a pane that does exist");
if (MAX_PANES !== 4) fail(`MAX_PANES is ${MAX_PANES}`);

const t = gridTemplate(60, 55);
console.log(`  template: ${t.gridTemplateColumns}  //  ${t.gridTemplateRows}`);

console.log(bad ? `\n${bad} failure(s)` : "\nall layout checks pass");
process.exit(bad ? 1 : 0);
