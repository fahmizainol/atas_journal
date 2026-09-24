// Entries coming faster than the pace window allows, on the one piece of chrome
// that is always on screen.
//
// It is a chip rather than only a line in `GuardMeters` because that panel is
// collapsed by default on both chart surfaces — the sim's side panel opens to
// 0x0 until you ask for it, and Live's routing panel can be closed, pinned or
// unmounted. The same argument `AccountChip` and `GuardChip` are on the bar for:
// a thing worth knowing before you press BUY must not depend on which panel
// happens to be open. `GuardMeters` still says it in full; this is the part that
// has to be unmissable.
//
// The quiet pill recipe the status chips use, not the roll guard's louder
// bordered one: the roll guard marks a tape that is *wrong*, and this marks a
// tape being read too quickly. Amber, because it is advice about the next entry
// rather than a state the account is in (see `RefusalFlash` for that split).
import { palette } from "../../theme";

/** Honest about where the evidence holds, in the tooltip, because the chip is
 *  shown on surfaces where the finding did *not* replicate — see
 *  `guardRules.paceRefusal`, which carries the numbers and the caveat. */
export function PaceChip({ reason }: { reason: string }) {
  return (
    <span
      data-pace-chip
      title={
        `Coming in fast — ${reason}.\n\n` +
        `Measured on the tape clock, which is the one that predicted: the same ` +
        `pace measured on the wall clock predicted nothing, in any corpus ` +
        `tested, at any threshold. Nothing is refused.\n\n` +
        `Where it holds: validated in backtest mode — the flagged trade averaged ` +
        `-0.183R against +0.005R for the rest (p=0.002). In replay and on the ` +
        `live book it pointed the OTHER way: the flagged trades were the better ` +
        `ones. Read it as a habit meter on those two, not as a verdict.`
      }
      style={{
        fontSize: 10,
        letterSpacing: 0.4,
        padding: "1px 6px",
        borderRadius: 10,
        border: `1px solid ${palette.orange}`,
        color: palette.orange,
        whiteSpace: "nowrap",
      }}
    >
      ⏱ FAST
    </span>
  );
}
