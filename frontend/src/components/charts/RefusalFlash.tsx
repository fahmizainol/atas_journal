// Being told no, said the same way on both terminals.
//
// Small, and the reason it is its own file is not reuse for its own sake. A
// refusal is the one message on either page whose whole job is to be *believed*
// — it is the app declining to do the thing you just asked for, and if the two
// pages dress that differently then one of them is training you to read the
// other one wrong. The replay exists to rehearse what the funded account will
// do; a rehearsal where the refusals look unfamiliar is a rehearsal of the
// wrong thing.
//
// Three rules, all inherited from what the two pages already did well:
//
//   - amber, not red. Red is for a state you are *in* (the day is over, the
//     account is blown); amber is for an answer to something you just tried.
//   - the sentence says why, in full, and never a code. Every refusal string in
//     this app is written to be read once and understood, because the moment it
//     is read is the moment somebody is annoyed.
//   - it is a reply, not a state. Whoever owns the string clears it — the
//     Simulator on a timer, Live when the draft changes — and this component
//     deliberately holds no timer of its own, so nothing can be dismissed out
//     from under a caller that still means it.

import { palette } from "../../theme";

export function RefusalFlash({
  reason,
  tone = "orange",
}: {
  reason: string | null;
  /** `red` for the handful of refusals that are also a state — a blown account
   *  refusing a new sitting is not a thing you retry in a minute. */
  tone?: "orange" | "red";
}) {
  if (!reason) return null;
  return (
    <div
      data-refusal
      role="status"
      style={{
        fontSize: 11,
        color: tone === "red" ? palette.red : palette.orange,
        marginTop: 4,
        lineHeight: 1.5,
      }}
    >
      ⚠ refused — {reason}
    </div>
  );
}
