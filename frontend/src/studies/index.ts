// Our own Pine studies — the ones we transcribed, as against the 415 we borrowed.
//
// Add one by writing a file next to this and listing it below. Everything else
// is already done: the picker gets a row, the legend gets an eye, an × and a
// settings panel generated from the script's own `input.*()` calls, each plot
// gets a colour knob, and the spec persists per pane. Nothing in this folder
// knows about any of that — see lib/pineStudy for why it doesn't have to.
//
// Loaded on demand with the community catalogue, in the same `loadCatalogue()`
// call and as a separate chunk. They could have been eager — the whole folder is
// a few kilobytes — but the runtime under them is not, and the entry bundle in
// front of a chart page that opens on a tape is not the place to spend 180 KB
// that most sittings never ask for.

import type { StudyEntry } from "../lib/studies";
import { emaPair } from "./emaPair";

export const MINE: StudyEntry[] = [emaPair];
